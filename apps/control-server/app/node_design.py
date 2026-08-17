"""Design Studio: Vision -> Outcome -> Capability -> Flow -> Evolution Node
(Issue #397, Phase 2 of the evolution control plane Epic #394).

The canonical contract is `docs/evolutionary-pipeline.md` (§4 ADR-7 for the
evaluation hierarchy, §8.2 for this phase's gate). Four things live here:

1. **Purpose-to-Node lineage** (`derive_node_lineage`). Phase 2 creates NO
   second Vision/Purpose model -- the Purpose Frame stays the projection
   `app/purpose_chain.py` recomputes from existing rows, and the connection
   to a Node is an ordinary `evolution_node_link`. What this module adds is
   the reading rule: **a link's `decision_method` already separates an AI
   PROPOSAL from a developer's CONFIRMATION**, so the lineage reports
   `relation_status='proposed'` for a `reasoning_llm` link and
   `'confirmed'` for a `manual` one. Neither is ever promoted to the other,
   and a Purpose element's own `confirmed`/`hypothesis`/`unknown` state is
   carried through unchanged -- confirming the Node's link says nothing
   about whether the Purpose element it points at was ever confirmed.

2. **Node decomposition** (`propose_decomposition`, `decide_candidate`). A
   reasoning model proposes whole CUTS of a scope, several at a time, so
   they can be compared -- comparing individual nodes across cuts would lose
   the only thing that distinguishes the cuts. Adopting one is a separate
   `decision_method: manual` record that creates the real Nodes through
   Phase 1's own `create_node`/`add_version`. The model never creates a
   Node, and a failed reasoning call stores nothing but the failure
   (Principle 6: no heuristic fallback).

3. **The three evaluation contracts** (`create_evaluation_policy`). ADR-7:
   Node, Flow/Capability and UX/Outcome are separate contracts connected by
   lineage, never composited. There is deliberately no score/weight/total
   anywhere in this module or its table -- a latency win must not be able to
   pay for a safety regression, and the only way to guarantee that is to
   have nowhere to write the combined number. `criteria` (what must be
   REACHED) and `floors` (what must not be BROKEN) are separate lists for
   the same reason: they are consumed at different moments, and merging them
   makes "we met the bar" and "we did not regress" indistinguishable.

4. **The Phase 3 handoff** (`assemble_handoff`). Deterministic assembly of
   references -- never copies. A copied criterion keeps reading as current
   after the policy it came from was superseded. A reference that cannot be
   resolved makes the bundle `incomplete` and is listed by name; it is never
   silently dropped, because a handoff that quietly lost its edge cases is
   indistinguishable from one that never had any.

Connection discipline (CLAUDE.md Implementation Constraints): every function
here takes an already-open `conn`, except `propose_decomposition`, which
takes NO connection at all -- it performs the LLM round trip and returns a
result the caller persists afterwards. Holding `get_conn()` across an LLM
call deadlocks the whole server.

probe-agent:
  role: Purpose-to-Node lineage, node decomposition proposals, the three evaluation contracts, and the Phase 3 design handoff
  capability: evolution-control-plane
  element_type: core
  operation_kind: mixed
  consumers: [control-server-routes]
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify an AI-proposed link never reads as a developer-confirmed relation, a failed decomposition run stores no candidates, adopting a cut is the only way a Node gets created, the three evaluation levels are never composited into a score, and an unresolvable handoff reference makes the bundle incomplete instead of disappearing.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, get_args

from pydantic import BaseModel, Field, ValidationError

from . import evolution_node
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

__all__ = [
    "EVALUATION_LEVELS",
    "CANDIDATE_DECISIONS",
    "RELATION_STATUSES",
    "ASSEMBLY_STATES",
    "DECOMPOSITION_PROMPT_VERSION",
    "DECOMPOSITION_SCHEMA_VERSION",
    "NodeDesignError",
    "NodeDesignNotFoundError",
    "NodeDesignValidationError",
    "NodeDesignConflictError",
    "ProposedNode",
    "DecompositionCandidate",
    "DecompositionResult",
    "derive_node_lineage",
    "propose_decomposition",
    "persist_decomposition",
    "decide_candidate",
    "create_evaluation_policy",
    "list_evaluation_policies",
    "assemble_handoff",
    "get_handoff",
]


# ---------------------------------------------------------------------------
# Finite vocabularies (Principle 6)
# ---------------------------------------------------------------------------

EvaluationLevel = Literal["node", "flow_capability", "ux_outcome"]
EVALUATION_LEVELS: Tuple[str, ...] = get_args(EvaluationLevel)

CandidateDecision = Literal["pending", "adopted", "held", "rejected"]
CANDIDATE_DECISIONS: Tuple[str, ...] = get_args(CandidateDecision)

# How a Node's link to a Purpose/Capability/Flow element was decided. These
# are READINGS of the existing `evolution_node_link.decision_method` column,
# not a second stored axis -- see the module docstring.
RelationStatus = Literal["proposed", "confirmed", "derived"]
RELATION_STATUSES: Tuple[str, ...] = get_args(RelationStatus)

AssemblyState = Literal["complete", "incomplete"]
ASSEMBLY_STATES: Tuple[str, ...] = get_args(AssemblyState)

DECOMPOSITION_PROMPT_VERSION = "node-decomposition-v1"
DECOMPOSITION_SCHEMA_VERSION = "node-decomposition-schema-v1"

# The link kinds that carry design lineage. A `component`/`probe_point`/
# `cell_binding` link is an implementation fact, not a design relation, and
# is deliberately excluded from the lineage projection.
_LINEAGE_LINK_KINDS: Tuple[str, ...] = ("purpose_element", "capability", "flow", "feature")

_DECISION_METHOD_TO_RELATION_STATUS: Dict[str, str] = {
    "manual": "confirmed",
    "reasoning_llm": "proposed",
    "deterministic": "derived",
}


class NodeDesignError(ValueError):
    """Base class for every failure this module raises."""


class NodeDesignNotFoundError(NodeDesignError):
    """A referenced row does not exist, or belongs to another System.

    The two are deliberately the same error: telling them apart would let a
    caller probe another System's ids.
    """


class NodeDesignValidationError(NodeDesignError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class NodeDesignConflictError(NodeDesignError):
    """The row exists but is not in a state where this operation is legal."""


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise NodeDesignValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


def _json_or_default(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A corrupted column is reported as its default rather than raising,
        # because a single bad row must not make the whole projection
        # unreadable -- but it is never silently "fixed" in place either.
        return default


# ---------------------------------------------------------------------------
# 1. Purpose-to-Node lineage
# ---------------------------------------------------------------------------


def derive_node_lineage(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_id: int,
    purpose_elements: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Project one Node's design lineage back towards the Purpose Frame.

    `purpose_elements` is the caller's already-computed
    `purpose_chain.derive_purpose_chain` element map, keyed by element id. It
    is passed IN rather than computed here for two reasons: deriving the
    Purpose Chain is a whole read of its own that the caller usually already
    has, and this function must stay callable with no Purpose Chain at all --
    a Node designed before any Purpose element was confirmed still has
    lineage worth showing, and its elements simply report as `unresolved`.

    Every hop keeps its own status. A `confirmed` link to an element whose
    own state is `unknown` is reported as exactly that -- a confirmed
    relation to an unconfirmed element -- and never collapsed into one word.
    """
    node = conn.execute(
        "SELECT * FROM evolution_node WHERE id = ? AND system_id = ?",
        (node_id, system_id),
    ).fetchone()
    if node is None:
        raise NodeDesignNotFoundError(f"Evolution Node {node_id} not found")

    rows = conn.execute(
        """SELECT * FROM evolution_node_link
               WHERE node_id = ? AND system_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
        (node_id, system_id),
    ).fetchall()

    relations: List[Dict[str, Any]] = []
    for row in rows:
        if row["link_kind"] not in _LINEAGE_LINK_KINDS:
            continue
        # The link's own decision_method IS the proposal/confirmation axis.
        # An unknown value is reported as-is rather than defaulted to
        # `confirmed`: guessing here would let an unrecognised provenance
        # read as a developer's judgement.
        relation_status = _DECISION_METHOD_TO_RELATION_STATUS.get(row["decision_method"])
        element_state = "unresolved"
        element_name = None
        if purpose_elements is not None:
            element = purpose_elements.get(row["target_ref"])
            if element is not None:
                element_state = element.get("state", "unresolved")
                element_name = element.get("name")
        relations.append(
            {
                "link_id": row["id"],
                "link_kind": row["link_kind"],
                "target_ref": row["target_ref"],
                "target_name": element_name,
                "relation_status": relation_status,
                "relation_decision_method": row["decision_method"],
                # The element's OWN state, independent of the relation's.
                "element_state": element_state,
                "note": row["note"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }
        )

    confirmed = [r for r in relations if r["relation_status"] == "confirmed"]
    proposed = [r for r in relations if r["relation_status"] == "proposed"]

    return {
        "system_id": system_id,
        "node_id": node_id,
        "node_key": node["node_key"],
        "maturity": node["maturity"],
        "relations": relations,
        # Counts, not a completeness score. #397 forbids collapsing design
        # state into a number, and a "lineage 60% complete" reading would be
        # exactly that.
        "confirmed_relation_count": len(confirmed),
        "proposed_relation_count": len(proposed),
        # `purpose_elements is None` is not the same as "no elements
        # matched": the first means the Purpose Frame was not supplied to
        # this call, the second that it was and nothing lined up (#380).
        "purpose_frame_supplied": purpose_elements is not None,
    }


# ---------------------------------------------------------------------------
# 2. Node decomposition
# ---------------------------------------------------------------------------


class _RawProposedNode(BaseModel):
    """One proposed Node inside a candidate cut.

    Every field #397 requires of a Node design is mandatory except the two
    that are legitimately empty: `dependencies` and `open_questions`. In
    particular `out_of_scope` is required -- a decomposition that only says
    what a node DOES leaves the boundary between two nodes undefined, which
    is the thing the developer is being asked to compare.
    """

    node_key: str = Field(min_length=1, max_length=80)
    display_name: str = Field(default="", max_length=200)
    mission: str = Field(min_length=1, max_length=2000)
    scope: str = Field(default="", max_length=2000)
    out_of_scope: str = Field(min_length=1, max_length=2000)
    input_contract: Dict[str, Any] = Field(default_factory=dict)
    output_contract: Dict[str, Any] = Field(default_factory=dict)
    side_effect_class: str
    trust_boundary: str
    dependencies: List[str] = Field(default_factory=list, max_length=20)
    observability: str = Field(default="", max_length=1000)
    open_questions: List[str] = Field(default_factory=list, max_length=20)


class _RawCandidate(BaseModel):
    candidate_key: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(default="", max_length=4000)
    nodes: List[_RawProposedNode] = Field(min_length=1, max_length=12)
    open_questions: List[str] = Field(default_factory=list, max_length=20)


class _RawDecompositionResponse(BaseModel):
    candidates: List[_RawCandidate] = Field(min_length=1, max_length=4)


@dataclass(frozen=True)
class ProposedNode:
    node_key: str
    display_name: str
    mission: str
    scope: str
    out_of_scope: str
    input_contract: Dict[str, Any]
    output_contract: Dict[str, Any]
    side_effect_class: str
    trust_boundary: str
    dependencies: Tuple[str, ...]
    observability: str
    open_questions: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node_key": self.node_key,
            "display_name": self.display_name,
            "mission": self.mission,
            "scope": self.scope,
            "out_of_scope": self.out_of_scope,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "side_effect_class": self.side_effect_class,
            "trust_boundary": self.trust_boundary,
            "dependencies": list(self.dependencies),
            "observability": self.observability,
            "open_questions": list(self.open_questions),
        }


@dataclass(frozen=True)
class DecompositionCandidate:
    candidate_key: str
    summary: str
    rationale: str
    nodes: Tuple[ProposedNode, ...]
    open_questions: Tuple[str, ...]


@dataclass(frozen=True)
class DecompositionResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = DECOMPOSITION_PROMPT_VERSION
    schema_version: str = DECOMPOSITION_SCHEMA_VERSION
    candidates: Tuple[DecompositionCandidate, ...] = ()
    error: Optional[str] = None


_SYSTEM_PROMPT = """\
You decompose a described scope of software behaviour into candidate
Evolution Nodes for a runtime evolution control plane.

An Evolution Node is a unit of processing that can evolve independently: it
has a business input/output contract, a responsibility, an explicit
NON-responsibility, a side-effect class, a trust boundary, and observable
behaviour. It is NOT a class, a file, or a function -- several functions may
implement one node, and one function may span two.

Produce 2 to 4 DIFFERENT candidate decompositions of the SAME scope, so a
developer can compare them. They must genuinely differ in where the cuts
fall (for example: fewer, larger nodes vs more, smaller ones; splitting by
data dependency vs by external boundary). Do not return near-identical cuts.

Rules you must follow:
- `out_of_scope` is required for every node. State what the node explicitly
  does NOT do. A decomposition that only says what each node does leaves the
  boundary between nodes undefined.
- `side_effect_class` must be one of: pure, read_only, local_write,
  external_write, irreversible.
- `trust_boundary` must be one of: internal, external_input, external_output,
  third_party.
- `node_key` must be a lowercase slug (letters, digits, hyphens).
- Put anything you are uncertain about in `open_questions`, at the node or
  the candidate level. Do not resolve an uncertainty by guessing: an unstated
  assumption is worse than a stated unknown.
- Do not invent facts about the codebase that the supplied context does not
  support.

Return ONLY a JSON object of the form:
{"candidates": [{"candidate_key": "...", "summary": "...", "rationale": "...",
  "open_questions": ["..."],
  "nodes": [{"node_key": "...", "display_name": "...", "mission": "...",
    "scope": "...", "out_of_scope": "...", "input_contract": {},
    "output_contract": {}, "side_effect_class": "...",
    "trust_boundary": "...", "dependencies": ["..."], "observability": "...",
    "open_questions": ["..."]}]}]}
"""


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def propose_decomposition(
    client: LLMClient,
    config: LLMConfig,
    *,
    scope_summary: str,
    context: str = "",
) -> DecompositionResult:
    """Ask a reasoning model for 2-4 candidate cuts of one scope.

    Takes NO database connection: this performs an external round trip, and
    holding `get_conn()` across it deadlocks the server process-wide
    (CLAUDE.md Implementation Constraints). The caller reads what it needs,
    closes the connection, calls this, then persists.

    Fail-closed in every failure mode (Principle 6). A mock client, a
    non-reasoning model, an API error, unparseable output, a schema
    violation, or ANY value outside the finite `side_effect_class` /
    `trust_boundary` vocabularies returns `error` set and no candidates. A
    partially-valid response is rejected whole rather than filtered down to
    its valid members: silently dropping the malformed candidate would
    present a 2-way comparison as if the model had only ever offered two.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return DecompositionResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Node decomposition requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    if not (scope_summary or "").strip():
        return DecompositionResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error="scope_summary must not be empty",
        )

    user_prompt = "\n\n".join(
        [
            "## Scope to decompose",
            scope_summary,
            "## Context (system understanding, flows, boundaries)",
            context or "(no additional context supplied)",
        ]
    )

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=4096,
        )
    except LLMError as exc:
        return DecompositionResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc)
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawDecompositionResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return DecompositionResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    candidates: List[DecompositionCandidate] = []
    seen_candidate_keys: set = set()
    for raw_candidate in validated.candidates:
        if raw_candidate.candidate_key in seen_candidate_keys:
            return DecompositionResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=f"Duplicate candidate_key: {raw_candidate.candidate_key!r}",
            )
        seen_candidate_keys.add(raw_candidate.candidate_key)

        nodes: List[ProposedNode] = []
        seen_node_keys: set = set()
        for raw_node in raw_candidate.nodes:
            # Mirror Phase 1's own empty-after-strip rules (`create_node`
            # rejects a blank node_key, `add_version` a blank mission).
            # `min_length=1` passes " ", so without these a candidate could
            # be STORED that adoption can never materialise -- and a retry
            # after the partial failure would 409 on the key collision
            # forever.
            if not raw_node.node_key.strip():
                return DecompositionResult(
                    provider=config.provider,
                    model=config.model,
                    is_mock=False,
                    error=(
                        "Model returned a whitespace-only node_key in candidate "
                        f"{raw_candidate.candidate_key!r}"
                    ),
                )
            if not raw_node.mission.strip():
                return DecompositionResult(
                    provider=config.provider,
                    model=config.model,
                    is_mock=False,
                    error=(
                        "Model returned a whitespace-only mission for node "
                        f"{raw_node.node_key!r}"
                    ),
                )
            if raw_node.side_effect_class not in evolution_node.SIDE_EFFECT_CLASSES:
                return DecompositionResult(
                    provider=config.provider,
                    model=config.model,
                    is_mock=False,
                    error=(
                        "Model returned an invalid side_effect_class: "
                        f"{raw_node.side_effect_class!r}"
                    ),
                )
            if raw_node.trust_boundary not in evolution_node.TRUST_BOUNDARIES:
                return DecompositionResult(
                    provider=config.provider,
                    model=config.model,
                    is_mock=False,
                    error=(
                        "Model returned an invalid trust_boundary: "
                        f"{raw_node.trust_boundary!r}"
                    ),
                )
            # Within ONE cut, two nodes with the same key are not a cut at
            # all -- the developer could not tell which one they adopted.
            if raw_node.node_key in seen_node_keys:
                return DecompositionResult(
                    provider=config.provider,
                    model=config.model,
                    is_mock=False,
                    error=(
                        "Duplicate node_key within candidate "
                        f"{raw_candidate.candidate_key!r}: {raw_node.node_key!r}"
                    ),
                )
            seen_node_keys.add(raw_node.node_key)
            nodes.append(
                ProposedNode(
                    node_key=raw_node.node_key,
                    display_name=raw_node.display_name or raw_node.node_key,
                    mission=raw_node.mission,
                    scope=raw_node.scope,
                    out_of_scope=raw_node.out_of_scope,
                    input_contract=dict(raw_node.input_contract),
                    output_contract=dict(raw_node.output_contract),
                    side_effect_class=raw_node.side_effect_class,
                    trust_boundary=raw_node.trust_boundary,
                    dependencies=tuple(raw_node.dependencies),
                    observability=raw_node.observability,
                    open_questions=tuple(raw_node.open_questions),
                )
            )
        candidates.append(
            DecompositionCandidate(
                candidate_key=raw_candidate.candidate_key,
                summary=raw_candidate.summary,
                rationale=raw_candidate.rationale,
                nodes=tuple(nodes),
                open_questions=tuple(raw_candidate.open_questions),
            )
        )

    return DecompositionResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        candidates=tuple(candidates),
    )


def persist_decomposition(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    result: DecompositionResult,
    scope_summary: str,
    session_id: Optional[int] = None,
    capability_ref: str = "",
    flow_ref: str = "",
    snapshot_id: Optional[int] = None,
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> Tuple[sqlite3.Row, List[sqlite3.Row]]:
    """Store a proposal and its candidates in one transaction.

    A FAILED result is still persisted -- as `status='failed'` with the error
    text and NO candidate rows. Recording the failure is what stops the same
    prompt being retried blind, and storing zero candidates is what keeps a
    heuristic fallback from ever appearing where a reasoning result belongs
    (Principle 6/7).
    """
    now = time.time()
    failed = result.error is not None
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO node_decomposition_proposal
                   (system_id, session_id, scope_summary, capability_ref, flow_ref,
                    snapshot_id, intelligence_run_id, status, error_details, is_mock,
                    created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, session_id, scope_summary, capability_ref, flow_ref,
                snapshot_id, intelligence_run_id,
                "failed" if failed else "proposed",
                result.error or "",
                1 if result.is_mock else 0,
                created_by, now,
            ),
        )
        proposal_id = cur.lastrowid

        candidate_rows: List[sqlite3.Row] = []
        if not failed:
            for candidate in result.candidates:
                conn.execute(
                    """INSERT INTO node_decomposition_candidate
                           (proposal_id, system_id, candidate_key, summary, rationale,
                            nodes_json, open_questions_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        proposal_id, system_id, candidate.candidate_key,
                        candidate.summary, candidate.rationale,
                        json.dumps([node.as_dict() for node in candidate.nodes]),
                        json.dumps(list(candidate.open_questions)),
                        now,
                    ),
                )
            candidate_rows = conn.execute(
                "SELECT * FROM node_decomposition_candidate "
                "WHERE proposal_id = ? ORDER BY id",
                (proposal_id,),
            ).fetchall()

        proposal = conn.execute(
            "SELECT * FROM node_decomposition_proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return proposal, candidate_rows


def decide_candidate(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    candidate_id: int,
    decision: str,
    decided_by: Optional[str] = None,
    note: str = "",
) -> Tuple[sqlite3.Row, List[sqlite3.Row]]:
    """Record the developer's judgement on ONE candidate cut.

    `adopted` is the only decision that creates anything: it materialises
    every proposed node through Phase 1's own `create_node` + `add_version`,
    so an adopted Node is indistinguishable from a hand-created one and gets
    the same append-only event log. The LLM never reaches this path -- the
    table CHECKs `decision_method = 'manual'`, and adoption is refused
    outright if the candidate has already been decided, so a second click
    cannot create a duplicate set of Nodes.

    A `node_key` that already exists in this System aborts the WHOLE
    adoption. Adopting a cut partially would leave the developer with some
    of one decomposition and some of another, which is not a decomposition
    they ever compared.
    """
    _check_membership(decision, CANDIDATE_DECISIONS, "decision")
    if decision == "pending":
        raise NodeDesignValidationError(
            "'pending' is the initial state, not a decision that can be recorded"
        )

    candidate = conn.execute(
        "SELECT * FROM node_decomposition_candidate WHERE id = ? AND system_id = ?",
        (candidate_id, system_id),
    ).fetchone()
    if candidate is None:
        raise NodeDesignNotFoundError(f"Decomposition candidate {candidate_id} not found")
    if candidate["decision"] != "pending":
        raise NodeDesignConflictError(
            f"Candidate {candidate_id} was already decided "
            f"({candidate['decision']}); decisions are not overwritten"
        )

    now = time.time()
    created_nodes: List[sqlite3.Row] = []

    if decision != "adopted":
        conn.execute("BEGIN")
        try:
            conn.execute(
                """UPDATE node_decomposition_candidate
                       SET decision = ?, decision_note = ?, decided_by = ?, decided_at = ?
                       WHERE id = ?""",
                (decision, note, decided_by, now, candidate_id),
            )
            row = conn.execute(
                "SELECT * FROM node_decomposition_candidate WHERE id = ?", (candidate_id,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return row, created_nodes

    proposed_nodes = _json_or_default(candidate["nodes_json"], [])
    if not proposed_nodes:
        raise NodeDesignConflictError(
            f"Candidate {candidate_id} carries no proposed nodes to adopt"
        )

    # `create_node` and `add_version` each run their own BEGIN/COMMIT, so the
    # adoption cannot be wrapped in one outer transaction here. The
    # pre-flight below is therefore load-bearing: it refuses the whole
    # adoption BEFORE anything is written. Besides the key collision, it
    # re-applies every rule Phase 1's `create_node`/`add_version` would
    # enforce mid-loop -- checked here against the exact values the creation
    # loop will pass, because a stored-but-invalid node failing on the
    # second iteration would leave the first node behind, keep the candidate
    # `pending`, and make every retry 409 on the now-existing key: a dead
    # end the developer cannot leave.
    seen_node_keys: set = set()
    for proposed in proposed_nodes:
        node_key = (proposed.get("node_key") or "").strip()
        if not node_key:
            raise NodeDesignValidationError(
                "A proposed node has no node_key; the candidate cannot be adopted"
            )
        raw_key = proposed.get("node_key")
        if raw_key in seen_node_keys:
            raise NodeDesignValidationError(
                f"node_key {raw_key!r} appears twice in this candidate; "
                "the candidate cannot be adopted"
            )
        seen_node_keys.add(raw_key)
        if not (proposed.get("mission") or "").strip():
            raise NodeDesignValidationError(
                f"Proposed node {node_key!r} has an empty mission; "
                "the candidate cannot be adopted"
            )
        side_effect_class = proposed.get("side_effect_class") or "read_only"
        if side_effect_class not in evolution_node.SIDE_EFFECT_CLASSES:
            raise NodeDesignValidationError(
                f"Proposed node {node_key!r} has an invalid side_effect_class "
                f"{side_effect_class!r}; the candidate cannot be adopted"
            )
        trust_boundary = proposed.get("trust_boundary") or "internal"
        if trust_boundary not in evolution_node.TRUST_BOUNDARIES:
            raise NodeDesignValidationError(
                f"Proposed node {node_key!r} has an invalid trust_boundary "
                f"{trust_boundary!r}; the candidate cannot be adopted"
            )
        # The collision check uses the RAW key -- it is what the creation
        # loop below passes to `create_node`, so it is what would collide.
        existing = conn.execute(
            "SELECT id FROM evolution_node WHERE system_id = ? AND node_key = ?",
            (system_id, raw_key),
        ).fetchone()
        if existing is not None:
            raise NodeDesignConflictError(
                f"node_key {raw_key!r} already exists in this System; "
                "adopting this candidate would only partially apply"
            )

    for proposed in proposed_nodes:
        node = evolution_node.create_node(
            conn,
            system_id=system_id,
            node_key=proposed["node_key"],
            display_name=proposed.get("display_name") or proposed["node_key"],
            created_by=decided_by,
        )
        evolution_node.add_version(
            conn,
            system_id=system_id,
            node_id=node["id"],
            mission=proposed.get("mission") or "",
            input_contract=proposed.get("input_contract") or {},
            output_contract=proposed.get("output_contract") or {},
            side_effect_class=proposed.get("side_effect_class") or "read_only",
            trust_boundary=proposed.get("trust_boundary") or "internal",
            scope=proposed.get("scope") or "",
            out_of_scope=proposed.get("out_of_scope") or "",
            # The contract version records the DEVELOPER's adoption, not the
            # model's authorship: the developer is who stands behind this
            # contract now. The model's own text stays visible on the
            # candidate row it came from.
            decision_method="manual",
            created_by=decided_by,
        )
        created_nodes.append(node)

    conn.execute(
        """UPDATE node_decomposition_candidate
               SET decision = 'adopted', decision_note = ?, decided_by = ?,
                   decided_at = ?, adopted_node_ids_json = ?
               WHERE id = ?""",
        (
            note, decided_by, now,
            json.dumps([node["id"] for node in created_nodes]),
            candidate_id,
        ),
    )
    row = conn.execute(
        "SELECT * FROM node_decomposition_candidate WHERE id = ?", (candidate_id,)
    ).fetchone()
    return row, created_nodes


# ---------------------------------------------------------------------------
# 3. The three evaluation contracts (ADR-7)
# ---------------------------------------------------------------------------


def create_evaluation_policy(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    policy_key: str,
    level: str,
    title: str = "",
    subject_ref: str = "",
    criteria: Optional[Sequence[Mapping[str, Any]]] = None,
    floors: Optional[Sequence[Mapping[str, Any]]] = None,
    unmeasured: Optional[Sequence[Mapping[str, Any]]] = None,
    decision_method: str = "manual",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Append a new version of ONE evaluation contract.

    `criteria` and `floors` stay separate arguments and separate columns
    (ADR-7): a criterion is a bar to reach before establishing, a floor is a
    property that must not regress afterwards. `unmeasured` is the third,
    equally load-bearing list -- an Outcome that cannot be measured is
    recorded WITH its reason rather than omitted, because an omitted
    criterion reads as "nothing to check here" (the #391 rule).

    There is no weight or score parameter, deliberately. See the module
    docstring.
    """
    _check_membership(level, EVALUATION_LEVELS, "level")
    _check_membership(
        decision_method, ("deterministic", "reasoning_llm", "manual"), "decision_method"
    )
    if not (policy_key or "").strip():
        raise NodeDesignValidationError("policy_key must not be empty")

    system_row = conn.execute(
        "SELECT id FROM systems WHERE id = ?", (system_id,)
    ).fetchone()
    if system_row is None:
        raise NodeDesignNotFoundError(f"System {system_id} not found")

    now = time.time()
    conn.execute("BEGIN")
    try:
        previous = conn.execute(
            """SELECT * FROM evolution_evaluation_policy
                   WHERE system_id = ? AND policy_key = ? AND superseded_by_id IS NULL
                   ORDER BY version_number DESC LIMIT 1""",
            (system_id, policy_key),
        ).fetchone()
        if previous is not None and previous["level"] != level:
            # Changing what a policy JUDGES is not a new version of the same
            # policy -- an establishment decision made against the node-level
            # version would silently start being checked against a flow-level
            # one. That needs a new policy_key.
            raise NodeDesignConflictError(
                f"policy_key {policy_key!r} already exists at level "
                f"{previous['level']!r}; a policy's level is part of what it is"
            )
        version_number = (previous["version_number"] + 1) if previous is not None else 1

        cur = conn.execute(
            """INSERT INTO evolution_evaluation_policy
                   (system_id, policy_key, level, version_number, title, subject_ref,
                    criteria_json, floors_json, unmeasured_json, decision_method,
                    created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, policy_key, level, version_number, title, subject_ref,
                json.dumps(list(criteria or [])),
                json.dumps(list(floors or [])),
                json.dumps(list(unmeasured or [])),
                decision_method, created_by, now,
            ),
        )
        policy_id = cur.lastrowid
        if previous is not None:
            conn.execute(
                "UPDATE evolution_evaluation_policy SET superseded_by_id = ? WHERE id = ?",
                (policy_id, previous["id"]),
            )
        row = conn.execute(
            "SELECT * FROM evolution_evaluation_policy WHERE id = ?", (policy_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def list_evaluation_policies(
    conn: sqlite3.Connection, *, system_id: int, level: Optional[str] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Return the CURRENT policies grouped by level.

    Grouped, never merged. The return shape has one key per level so a
    consumer cannot iterate a flat list and accidentally treat a UX/Outcome
    criterion as a Node one -- the grouping is the ADR-7 separation made
    structural rather than documented.
    """
    if level is not None:
        _check_membership(level, EVALUATION_LEVELS, "level")

    rows = conn.execute(
        """SELECT * FROM evolution_evaluation_policy
               WHERE system_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
        (system_id,),
    ).fetchall()

    grouped: Dict[str, List[Dict[str, Any]]] = {name: [] for name in EVALUATION_LEVELS}
    for row in rows:
        if level is not None and row["level"] != level:
            continue
        grouped[row["level"]].append(
            {
                "id": row["id"],
                "policy_key": row["policy_key"],
                "level": row["level"],
                "version_number": row["version_number"],
                "title": row["title"],
                "subject_ref": row["subject_ref"],
                "criteria": _json_or_default(row["criteria_json"], []),
                "floors": _json_or_default(row["floors_json"], []),
                "unmeasured": _json_or_default(row["unmeasured_json"], []),
                "decision_method": row["decision_method"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }
        )
    return grouped


# ---------------------------------------------------------------------------
# 4. The Phase 3 handoff
# ---------------------------------------------------------------------------


def assemble_handoff(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_ids: Sequence[int],
    evaluation_policy_ids: Sequence[int] = (),
    dataset_refs: Sequence[str] = (),
    probe_plan_id: Optional[int] = None,
    establishment_criteria_draft: Sequence[str] = (),
    reopen_criteria_draft: Sequence[str] = (),
    exploration_brief: str = "",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Assemble the bundle Phase 3 explores against.

    Every id is resolved against THIS System before it is stored. An id that
    does not resolve is recorded by name in `missing_refs` and makes the
    bundle `incomplete` -- it is never dropped, because a handoff that
    quietly lost its failure dataset is indistinguishable from one that
    never had one, and Phase 3 would then explore a narrower problem than
    the developer designed.

    Only references are stored, never copies of the referenced content: a
    copied criterion keeps reading as current after the policy it came from
    is superseded.
    """
    if not node_ids:
        raise NodeDesignValidationError("A handoff must reference at least one Node")

    missing: List[str] = []

    resolved_nodes: List[int] = []
    for node_id in node_ids:
        row = conn.execute(
            "SELECT id FROM evolution_node WHERE id = ? AND system_id = ?",
            (node_id, system_id),
        ).fetchone()
        if row is None:
            missing.append(f"node:{node_id}")
        else:
            resolved_nodes.append(node_id)

    resolved_policies: List[int] = []
    for policy_id in evaluation_policy_ids:
        row = conn.execute(
            "SELECT id FROM evolution_evaluation_policy WHERE id = ? AND system_id = ?",
            (policy_id, system_id),
        ).fetchone()
        if row is None:
            missing.append(f"evaluation_policy:{policy_id}")
        else:
            resolved_policies.append(policy_id)

    resolved_probe_plan: Optional[int] = None
    if probe_plan_id is not None:
        row = conn.execute(
            "SELECT id FROM probe_plans WHERE id = ? AND system_id = ?",
            (probe_plan_id, system_id),
        ).fetchone()
        if row is None:
            missing.append(f"probe_plan:{probe_plan_id}")
        else:
            resolved_probe_plan = probe_plan_id

    if not resolved_nodes:
        raise NodeDesignNotFoundError(
            "None of the referenced Nodes exist in this System"
        )

    now = time.time()
    cur = conn.execute(
        """INSERT INTO evolution_design_handoff
               (system_id, node_ids_json, evaluation_policy_ids_json, dataset_refs_json,
                probe_plan_id, establishment_criteria_draft_json,
                reopen_criteria_draft_json, exploration_brief, assembly_state,
                missing_refs_json, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id,
            json.dumps(resolved_nodes),
            json.dumps(resolved_policies),
            json.dumps(list(dataset_refs)),
            resolved_probe_plan,
            json.dumps(list(establishment_criteria_draft)),
            json.dumps(list(reopen_criteria_draft)),
            exploration_brief,
            "incomplete" if missing else "complete",
            json.dumps(missing),
            created_by,
            now,
        ),
    )
    return conn.execute(
        "SELECT * FROM evolution_design_handoff WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def get_handoff(
    conn: sqlite3.Connection, *, system_id: int, handoff_id: int
) -> Dict[str, Any]:
    """Read one handoff, resolving its references at READ time.

    Resolution happens here rather than at write time so a Node deleted or a
    policy superseded after assembly shows up as it actually is now. That is
    the same discipline #336 applies to the evidence feed: currency is not
    knowable at publish time.
    """
    row = conn.execute(
        "SELECT * FROM evolution_design_handoff WHERE id = ? AND system_id = ?",
        (handoff_id, system_id),
    ).fetchone()
    if row is None:
        raise NodeDesignNotFoundError(f"Design handoff {handoff_id} not found")

    node_ids = _json_or_default(row["node_ids_json"], [])
    policy_ids = _json_or_default(row["evaluation_policy_ids_json"], [])

    nodes: List[Dict[str, Any]] = []
    for node_id in node_ids:
        node_row = conn.execute(
            "SELECT id, node_key, maturity FROM evolution_node "
            "WHERE id = ? AND system_id = ?",
            (node_id, system_id),
        ).fetchone()
        nodes.append(
            {"id": node_id, "resolved": node_row is not None,
             "node_key": node_row["node_key"] if node_row else None,
             "maturity": node_row["maturity"] if node_row else None}
        )

    policies: List[Dict[str, Any]] = []
    for policy_id in policy_ids:
        policy_row = conn.execute(
            "SELECT id, policy_key, level, superseded_by_id "
            "FROM evolution_evaluation_policy WHERE id = ? AND system_id = ?",
            (policy_id, system_id),
        ).fetchone()
        policies.append(
            {
                "id": policy_id,
                "resolved": policy_row is not None,
                "policy_key": policy_row["policy_key"] if policy_row else None,
                "level": policy_row["level"] if policy_row else None,
                # A superseded policy is still readable -- the handoff
                # references the exact version a decision was designed
                # against -- but the reader has to be told it moved on.
                "superseded": (
                    policy_row["superseded_by_id"] is not None if policy_row else None
                ),
            }
        )

    return {
        "id": row["id"],
        "system_id": system_id,
        "session_id": row["session_id"],
        "nodes": nodes,
        "evaluation_policies": policies,
        "dataset_refs": _json_or_default(row["dataset_refs_json"], []),
        "probe_plan_id": row["probe_plan_id"],
        "establishment_criteria_draft": _json_or_default(
            row["establishment_criteria_draft_json"], []
        ),
        "reopen_criteria_draft": _json_or_default(row["reopen_criteria_draft_json"], []),
        "exploration_brief": row["exploration_brief"],
        "assembly_state": row["assembly_state"],
        "missing_refs": _json_or_default(row["missing_refs_json"], []),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }
