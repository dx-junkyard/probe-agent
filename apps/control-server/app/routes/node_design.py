"""Design Studio API: Purpose-to-Node lineage, node decomposition, the three
evaluation contracts, and the Phase 3 handoff
(Issue #397, Phase 2 of the evolution control plane Epic #394).

The domain layer is `app/node_design.py`; the canonical contract is
`docs/evolutionary-pipeline.md`. Two boundary rules are load-bearing here:

- **The decomposition endpoint reads, closes the connection, calls the model,
  then reopens to persist.** Holding `get_conn()` across an LLM round trip
  deadlocks the whole server: the lock is process-wide and non-reentrant,
  and the LLM client opens its own connection to consume System quota
  (CLAUDE.md Implementation Constraints).
- **A failed reasoning run is a 502 AND a persisted failure row.** It never
  falls back to a heuristic decomposition and never returns candidates
  (Principle 6). The row exists so the same prompt is not retried blind.

Adopting a candidate is the only path that creates Evolution Nodes, and it
is `decision_method: manual` end to end -- the model's output is a proposal
until a named human adopts it (Principle 7).

probe-agent:
  role: API boundary for the Design Studio -- lineage, decomposition proposals and decisions, evaluation contracts, design handoff
  capability: evolution-control-plane
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify the reasoning call never runs with a database connection open, a failed run persists a failure and no candidates, only an explicit human adoption creates Nodes, and the three evaluation levels are returned grouped rather than merged.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import evolution_node, node_design, purpose_chain
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..llm import LLMConfig, create_llm_client
from ..models import (
    DecompositionCandidateOut,
    DecompositionDecisionIn,
    DecompositionDecisionOut,
    DecompositionProposalOut,
    DecompositionProposeIn,
    EvaluationPoliciesOut,
    EvaluationPolicyCreateIn,
    EvaluationPolicyOut,
    NodeDesignHandoffCreateIn,
    NodeDesignHandoffOut,
    NodeLineageOut,
)

router = APIRouter(prefix="/node-design", tags=["node-design"])

_ERROR_STATUS = {
    node_design.NodeDesignNotFoundError: 404,
    node_design.NodeDesignConflictError: 409,
    node_design.NodeDesignValidationError: 422,
    # Adoption calls into Phase 1's `create_node`/`add_version`. Their
    # domain errors are mapped too, so a rule enforced there but missed by
    # the adoption pre-flight surfaces as a client error instead of a 500.
    evolution_node.EvolutionNodeNotFoundError: 404,
    evolution_node.EvolutionNodeConflictError: 409,
    evolution_node.EvolutionNodeValidationError: 422,
}


def _raise_domain_error(exc: ValueError) -> None:
    for exc_type, status_code in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _candidate_out(row) -> DecompositionCandidateOut:
    return DecompositionCandidateOut(
        id=row["id"],
        proposal_id=row["proposal_id"],
        candidate_key=row["candidate_key"],
        summary=row["summary"],
        rationale=row["rationale"],
        nodes=node_design._json_or_default(row["nodes_json"], []),
        open_questions=node_design._json_or_default(row["open_questions_json"], []),
        decision=row["decision"],
        decision_note=row["decision_note"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        adopted_node_ids=node_design._json_or_default(row["adopted_node_ids_json"], []),
        created_at=row["created_at"],
    )


def _proposal_out(row, candidates) -> DecompositionProposalOut:
    return DecompositionProposalOut(
        id=row["id"],
        system_id=row["system_id"],
        session_id=row["session_id"],
        scope_summary=row["scope_summary"],
        capability_ref=row["capability_ref"],
        flow_ref=row["flow_ref"],
        snapshot_id=row["snapshot_id"],
        intelligence_run_id=row["intelligence_run_id"],
        status=row["status"],
        error_details=row["error_details"],
        is_mock=bool(row["is_mock"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        candidates=[_candidate_out(candidate) for candidate in candidates],
    )


def _policy_out(policy: dict) -> EvaluationPolicyOut:
    return EvaluationPolicyOut(**policy)


# ---------------------------------------------------------------------------
# 1. Purpose-to-Node lineage
# ---------------------------------------------------------------------------


@router.get("/nodes/{node_id}/lineage", response_model=NodeLineageOut)
def get_node_lineage(
    node_id: int,
    session_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> NodeLineageOut:
    """One Node's design lineage back towards the Purpose Frame.

    `session_id` is optional because the lineage must be readable with no
    Purpose Frame at all -- a Node designed before any Purpose element was
    confirmed still has links worth showing. When it is absent the response
    reports `purpose_frame_supplied: false` rather than pretending the
    elements were checked and found missing (#380).
    """
    with get_conn() as conn:
        elements = None
        if session_id is not None:
            try:
                chain = purpose_chain.derive_purpose_chain(conn, system_id, session_id)
            except Exception as exc:  # noqa: BLE001 - reported, never guessed
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not derive the Purpose Frame for this session: {exc}",
                ) from exc
            # `state` is the element's OWN confirmation state, carried through
            # unchanged. It is never combined with the link's decision_method
            # -- a confirmed link to an `unknown` element must keep reading as
            # exactly that.
            elements = {
                element.id: {
                    "state": element.state,
                    "name": element.display_statement or element.statement,
                }
                for element in chain.elements
            }
        try:
            lineage = node_design.derive_node_lineage(
                conn, system_id=system_id, node_id=node_id, purpose_elements=elements
            )
        except node_design.NodeDesignError as exc:
            _raise_domain_error(exc)
    return NodeLineageOut(**lineage)


# ---------------------------------------------------------------------------
# 2. Node decomposition
# ---------------------------------------------------------------------------


@router.post("/decompositions", response_model=DecompositionProposalOut, status_code=201)
def propose_decomposition(
    payload: DecompositionProposeIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> DecompositionProposalOut:
    """Ask a reasoning model for 2-4 candidate cuts of one scope.

    The connection is deliberately opened three times, not once: read the
    snapshot id, CLOSE, call the model, reopen to persist. This is not a
    style choice -- the connection lock is process-wide and the LLM client
    opens its own connection for quota, so one call inside `with get_conn()`
    hangs the entire server until it is restarted.
    """
    # (1) read what the call needs, then close.
    with get_conn() as conn:
        system_row = conn.execute(
            "SELECT id FROM systems WHERE id = ?", (system_id,)
        ).fetchone()
        if system_row is None:
            raise HTTPException(status_code=404, detail="System not found")
        snapshot_row = conn.execute(
            """SELECT id FROM repository_snapshots
                   WHERE system_id = ? ORDER BY id DESC LIMIT 1""",
            (system_id,),
        ).fetchone()
        snapshot_id = snapshot_row["id"] if snapshot_row is not None else None

    # (2) the external round trip, with no connection held.
    config = LLMConfig.intelligence_from_env()
    client = create_llm_client(config)
    started_at = time.time()
    result = node_design.propose_decomposition(
        client,
        config,
        scope_summary=payload.scope_summary,
        context=payload.context,
    )
    completed_at = time.time()

    # (3) reopen and persist -- including a failure, which is a fact worth
    # keeping rather than an error to discard.
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version,
                    schema_version, decision_method, status, error_details, is_mock,
                    started_at, completed_at)
               VALUES (?, ?, 'node_decomposition', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)""",
            (
                system_id, snapshot_id, result.provider, result.model,
                result.prompt_version, result.schema_version,
                "failed" if result.error else "completed",
                result.error,
                1 if result.is_mock else 0,
                started_at, completed_at,
            ),
        )
        run_id = cur.lastrowid
        proposal, candidates = node_design.persist_decomposition(
            conn,
            system_id=system_id,
            result=result,
            scope_summary=payload.scope_summary,
            session_id=payload.session_id,
            capability_ref=payload.capability_ref,
            flow_ref=payload.flow_ref,
            snapshot_id=snapshot_id,
            intelligence_run_id=run_id,
            created_by=principal.username,
        )

    if result.error:
        # 502, not 500: the failure is upstream (or a refusal to accept
        # unusable output), and the persisted proposal id lets the developer
        # see exactly what was attempted.
        raise HTTPException(
            status_code=502,
            detail={
                "code": "decomposition_failed",
                "message": result.error,
                "proposal_id": proposal["id"],
            },
        )
    return _proposal_out(proposal, candidates)


@router.get("/decompositions/{proposal_id}", response_model=DecompositionProposalOut)
def get_decomposition(
    proposal_id: int,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> DecompositionProposalOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM node_decomposition_proposal WHERE id = ? AND system_id = ?",
            (proposal_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Decomposition proposal not found")
        candidates = conn.execute(
            "SELECT * FROM node_decomposition_candidate WHERE proposal_id = ? ORDER BY id",
            (proposal_id,),
        ).fetchall()
    return _proposal_out(row, candidates)


@router.post(
    "/decomposition-candidates/{candidate_id}/decision",
    response_model=DecompositionDecisionOut,
)
def decide_decomposition_candidate(
    candidate_id: int,
    payload: DecompositionDecisionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> DecompositionDecisionOut:
    """Record the developer's judgement on one candidate cut.

    `adopted` is the only decision that creates Evolution Nodes, and it is
    the human gate this whole phase turns on: the model proposed the cut, a
    named person adopts it. A candidate that was already decided is a 409 --
    decisions are not overwritten, and a second adoption would create a
    duplicate set of Nodes.
    """
    with get_conn() as conn:
        try:
            candidate, created_nodes = node_design.decide_candidate(
                conn,
                system_id=system_id,
                candidate_id=candidate_id,
                decision=payload.decision,
                decided_by=principal.username,
                note=payload.note,
            )
        except (node_design.NodeDesignError, evolution_node.EvolutionNodeError) as exc:
            _raise_domain_error(exc)
        created = [
            {"id": node["id"], "node_key": node["node_key"], "maturity": node["maturity"]}
            for node in created_nodes
        ]
    return DecompositionDecisionOut(
        candidate=_candidate_out(candidate), created_nodes=created
    )


# ---------------------------------------------------------------------------
# 3. The three evaluation contracts
# ---------------------------------------------------------------------------


@router.post("/evaluation-policies", response_model=EvaluationPolicyOut, status_code=201)
def create_evaluation_policy(
    payload: EvaluationPolicyCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EvaluationPolicyOut:
    with get_conn() as conn:
        try:
            row = node_design.create_evaluation_policy(
                conn,
                system_id=system_id,
                policy_key=payload.policy_key,
                level=payload.level,
                title=payload.title,
                subject_ref=payload.subject_ref,
                criteria=[c.model_dump() for c in payload.criteria],
                floors=[f.model_dump() for f in payload.floors],
                unmeasured=[u.model_dump() for u in payload.unmeasured],
                created_by=principal.username,
            )
        except node_design.NodeDesignError as exc:
            _raise_domain_error(exc)
        policies = node_design.list_evaluation_policies(conn, system_id=system_id)
    for level_policies in policies.values():
        for policy in level_policies:
            if policy["id"] == row["id"]:
                return _policy_out(policy)
    raise HTTPException(status_code=500, detail="Policy was created but could not be read back")


@router.get("/evaluation-policies", response_model=EvaluationPoliciesOut)
def list_evaluation_policies(
    level: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> EvaluationPoliciesOut:
    """The current evaluation contracts, GROUPED by level, never merged.

    The grouping is ADR-7 made structural: a consumer that iterated a flat
    list could treat a UX/Outcome criterion as a Node one, and the whole
    point of the three contracts is that they are not interchangeable.
    """
    if level is not None and level not in node_design.EVALUATION_LEVELS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unknown_evaluation_level",
                "message": "level must be one of: "
                + ", ".join(node_design.EVALUATION_LEVELS),
            },
        )
    with get_conn() as conn:
        grouped = node_design.list_evaluation_policies(
            conn, system_id=system_id, level=level
        )
    return EvaluationPoliciesOut(
        node=[_policy_out(p) for p in grouped["node"]],
        flow_capability=[_policy_out(p) for p in grouped["flow_capability"]],
        ux_outcome=[_policy_out(p) for p in grouped["ux_outcome"]],
    )


# ---------------------------------------------------------------------------
# 4. The Phase 3 handoff
# ---------------------------------------------------------------------------


@router.post("/handoffs", response_model=NodeDesignHandoffOut, status_code=201)
def create_design_handoff(
    payload: NodeDesignHandoffCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> NodeDesignHandoffOut:
    with get_conn() as conn:
        try:
            row = node_design.assemble_handoff(
                conn,
                system_id=system_id,
                node_ids=payload.node_ids,
                evaluation_policy_ids=payload.evaluation_policy_ids,
                dataset_refs=payload.dataset_refs,
                probe_plan_id=payload.probe_plan_id,
                establishment_criteria_draft=payload.establishment_criteria_draft,
                reopen_criteria_draft=payload.reopen_criteria_draft,
                exploration_brief=payload.exploration_brief,
                created_by=principal.username,
            )
            handoff = node_design.get_handoff(
                conn, system_id=system_id, handoff_id=row["id"]
            )
        except node_design.NodeDesignError as exc:
            _raise_domain_error(exc)
    return NodeDesignHandoffOut(**handoff)


@router.get("/handoffs/{handoff_id}", response_model=NodeDesignHandoffOut)
def get_design_handoff(
    handoff_id: int,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> NodeDesignHandoffOut:
    with get_conn() as conn:
        try:
            handoff = node_design.get_handoff(
                conn, system_id=system_id, handoff_id=handoff_id
            )
        except node_design.NodeDesignError as exc:
            _raise_domain_error(exc)
    return NodeDesignHandoffOut(**handoff)
