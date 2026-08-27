"""UX Design Lineage: Solution Design API (Epic #405, Issue #408).

The domain layer is `app/solution_design.py`; the canonical contract is
`docs/ux-design-lineage.md` §3. This module is a thin I/O boundary:

* `system_id` always comes from `Depends(get_system_id)`; every write also
  depends on `Depends(require_user)` and derives its actor from the
  authenticated `Principal` -- never from the request body (the same rule
  `routes/purpose_chain.py` documents: an unverifiable body-supplied
  identity lets a caller fabricate an audit trail). GET endpoints write
  nothing.
* Finite rejection codes are reported as `detail={"code": ..., "message":
  ...}` with a Japanese message, split across 404 (not found / foreign
  System) / 409 (conflicting state, e.g. an already-adopted Option) / 422
  (validation, e.g. an empty key or a `static_flow` link with no snapshot).
  `app.solution_design`'s own errors carry their code as a
  `"<code>: <message>"` prefix; `app.evolution_node`'s reused
  `_require_approved_probe_point` gate is mapped to its own three codes here
  so the Probe Point rule reads the same way even though its exceptions
  aren't Solution-Design-owned.
* No endpoint calls a reasoning model. Nothing here holds a `get_conn()`
  connection across a subprocess or network call -- there isn't one to hold.

probe-agent:
  role: API boundary for Solution Design CRUD, the Requirement bridge, exclusive-choice adoption, target-implementation links, change-origin classification, and the read-only handoff
  capability: ux-design-lineage
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify decided_by/created_by always come from the authenticated Principal rather than the request body, that a second `adopt` while one Option is already adopted returns 409 without mutating the incumbent, and that every finite rejection maps to the documented status code and code string.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import evolution_node, solution_design
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    SolutionDesignChangeOriginsOut,
    SolutionDesignCreateRequest,
    SolutionDesignDecisionOut,
    SolutionDesignDetailOut,
    SolutionDesignHandoffOut,
    SolutionDesignListOut,
    SolutionDesignOptionCreateRequest,
    SolutionDesignOptionDecisionCreateRequest,
    SolutionDesignOptionOut,
    SolutionDesignOut,
    SolutionDesignRequirementLinkCreateRequest,
    SolutionDesignRequirementLinkOut,
    SolutionDesignTargetLinkCreateRequest,
    SolutionDesignTargetLinkOut,
)

router = APIRouter(prefix="/solution-designs", tags=["solution-design"])

#: Every finite code `app/solution_design.py` may prefix a message with,
#: mapped to its HTTP status. Kept here (rather than duplicated per raise
#: site) so the 404/409/422 split has exactly one table.
_STATUS_BY_CODE = {
    "solution_design_key_required": 422,
    "solution_design_option_key_required": 422,
    "solution_design_invalid_authored_by_kind": 422,
    "solution_design_invalid_decision_method": 422,
    "solution_design_invalid_decision": 422,
    "solution_design_invalid_target_kind": 422,
    "solution_design_target_ref_required": 422,
    "flow_target_requires_snapshot": 422,
    "out_of_scope_requirement_not_implementable": 422,
    "solution_design_not_found": 404,
    "solution_design_option_not_found": 404,
    "requirement_not_found": 404,
    "snapshot_not_found": 404,
    "system_not_found": 404,
    "solution_design_key_conflict": 409,
    "solution_design_option_already_adopted": 409,
}

#: The Probe Point write-time gate is reused from `app/evolution_node.py`
#: (`_require_approved_probe_point`), whose exceptions are not
#: Solution-Design-owned and so carry no `"<code>: ..."` prefix. Mapped by
#: exception type instead, to (code, status, Japanese message).
_PROBE_POINT_ERROR_MAP = {
    evolution_node.EvolutionNodeNotFoundError: (
        "probe_point_not_found", 404, "指定された Probe Point が見つかりません。"
    ),
    evolution_node.EvolutionNodeConflictError: (
        "probe_point_not_approved",
        409,
        "指定された Probe Point は承認されていないため、target link の対象にできません。",
    ),
    evolution_node.EvolutionNodeValidationError: (
        "probe_point_ref_invalid", 422, None  # message filled from str(exc)
    ),
}


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for every write in this module.
    Never a body-supplied value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


def _raise_domain_error(exc: ValueError) -> None:
    for exc_type, (code, status_code, fixed_message) in _PROBE_POINT_ERROR_MAP.items():
        if isinstance(exc, exc_type):
            message = fixed_message if fixed_message is not None else str(exc)
            raise HTTPException(
                status_code=status_code, detail={"code": code, "message": message}
            ) from exc
    text = str(exc)
    code, _, rest = text.partition(": ")
    if code in _STATUS_BY_CODE:
        raise HTTPException(
            status_code=_STATUS_BY_CODE[code],
            detail={"code": code, "message": rest or text},
        ) from exc
    raise HTTPException(
        status_code=422, detail={"code": "solution_design_error", "message": text}
    ) from exc


@router.get("", response_model=SolutionDesignListOut)
def list_solution_designs(system_id: int = Depends(get_system_id)) -> SolutionDesignListOut:
    with get_conn() as conn:
        result = solution_design.list_designs(conn, system_id=system_id)
    return SolutionDesignListOut(**result)


@router.post("", response_model=SolutionDesignOut, status_code=201)
def create_solution_design(
    payload: SolutionDesignCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> SolutionDesignOut:
    try:
        with get_conn() as conn:
            row = solution_design.create_design(
                conn,
                system_id=system_id,
                design_key=payload.design_key,
                title=payload.title,
                summary=payload.summary,
                created_by=_principal_actor(principal),
            )
            out = solution_design._design_out_dict(conn, system_id, row)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignOut(**out)


@router.get("/{design_key}", response_model=SolutionDesignDetailOut)
def get_solution_design(
    design_key: str, system_id: int = Depends(get_system_id)
) -> SolutionDesignDetailOut:
    try:
        with get_conn() as conn:
            out = solution_design.get_design_detail(conn, system_id=system_id, design_key=design_key)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignDetailOut(**out)


@router.post("/{design_key}/options", response_model=SolutionDesignOptionOut, status_code=201)
def add_solution_design_option(
    design_key: str,
    payload: SolutionDesignOptionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> SolutionDesignOptionOut:
    try:
        with get_conn() as conn:
            design = solution_design._find_design_by_key(conn, system_id, design_key)
            row = solution_design.add_option(
                conn,
                system_id=system_id,
                solution_design_id=design["id"],
                option_key=payload.option_key,
                option_order=payload.option_order,
                title=payload.title,
                approach=payload.approach,
                tradeoffs=payload.tradeoffs,
                risks=payload.risks,
                created_by=_principal_actor(principal),
            )
            out = solution_design._option_out_dict(conn, system_id, design["id"], row)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignOptionOut(**out)


@router.post(
    "/{design_key}/requirement-links",
    response_model=SolutionDesignRequirementLinkOut,
    status_code=201,
)
def add_solution_design_requirement_link(
    design_key: str,
    payload: SolutionDesignRequirementLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> SolutionDesignRequirementLinkOut:
    try:
        with get_conn() as conn:
            design = solution_design._find_design_by_key(conn, system_id, design_key)
            row = solution_design.add_requirement_link(
                conn,
                system_id=system_id,
                solution_design_id=design["id"],
                requirement_key=payload.requirement_key,
                note=payload.note,
                created_by=_principal_actor(principal),
            )
            out = solution_design._requirement_link_out_dict(conn, system_id, row)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignRequirementLinkOut(**out)


@router.post(
    "/{design_key}/target-links", response_model=SolutionDesignTargetLinkOut, status_code=201
)
def add_solution_design_target_link(
    design_key: str,
    payload: SolutionDesignTargetLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> SolutionDesignTargetLinkOut:
    try:
        with get_conn() as conn:
            design = solution_design._find_design_by_key(conn, system_id, design_key)
            row = solution_design.add_target_link(
                conn,
                system_id=system_id,
                solution_design_id=design["id"],
                option_key=payload.option_key,
                target_kind=payload.target_kind,
                target_ref=payload.target_ref,
                captured_snapshot_id=payload.captured_snapshot_id,
                note=payload.note,
                created_by=_principal_actor(principal),
            )
            out = solution_design._target_link_out_dict(conn, system_id, row)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignTargetLinkOut(**out)


@router.post("/{design_key}/decisions", response_model=SolutionDesignDecisionOut, status_code=201)
def record_solution_design_decision(
    design_key: str,
    payload: SolutionDesignOptionDecisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> SolutionDesignDecisionOut:
    try:
        with get_conn() as conn:
            design = solution_design._find_design_by_key(conn, system_id, design_key)
            row = solution_design.record_option_decision(
                conn,
                system_id=system_id,
                solution_design_id=design["id"],
                option_key=payload.option_key,
                decision=payload.decision,
                rationale=payload.rationale,
                decided_by=_principal_actor(principal),
            )
            out = solution_design._decision_out_dict(row)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignDecisionOut(**out)


@router.get("/{design_key}/change-origins", response_model=SolutionDesignChangeOriginsOut)
def get_solution_design_change_origins(
    design_key: str, system_id: int = Depends(get_system_id)
) -> SolutionDesignChangeOriginsOut:
    try:
        with get_conn() as conn:
            out = solution_design.get_change_origins(conn, system_id=system_id, design_key=design_key)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignChangeOriginsOut(**out)


@router.get("/{design_key}/handoff", response_model=SolutionDesignHandoffOut)
def get_solution_design_handoff(
    design_key: str, system_id: int = Depends(get_system_id)
) -> SolutionDesignHandoffOut:
    try:
        with get_conn() as conn:
            out = solution_design.get_handoff(conn, system_id=system_id, design_key=design_key)
    except ValueError as exc:
        _raise_domain_error(exc)
    return SolutionDesignHandoffOut(**out)
