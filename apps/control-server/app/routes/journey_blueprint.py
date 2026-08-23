"""Journey Service Blueprint API (Issue #423, Epic #418).

`docs/stakeholder-value-network.md` §8/§10 is the endpoint contract this
module implements against `app/journey_blueprint.py`'s deterministic domain
service. What this boundary deliberately does NOT do:

* It never accepts `created_by` / `decision_method` from a request body --
  every write request model is `ConfigDict(extra="forbid")` and carries
  neither field; they are always derived from the route path and the
  authenticated `Principal` (#337's rule, `routes/ux_design.py`'s /
  `routes/stakeholder_network.py`'s identical rule).
* GET writes nothing -- a page view is never a decision (§0 invariant 9 /
  #382's rule, applied here).

probe-agent:
  role: API boundary for the Journey Service Blueprint projection and its stakeholder/delivery/exchange link tables
  capability: journey-service-blueprint
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify every write derives created_by/decision_method from the authenticated Principal rather than the request body, that GET never mutates state, and that the blueprint never calls an LLM.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import journey_blueprint as jb
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    BlueprintDiffOut,
    BlueprintOut,
    JourneyStepDeliveryLinkCreateRequest,
    JourneyStepDeliveryLinkOut,
    JourneyStepExchangeLinkCreateRequest,
    JourneyStepExchangeLinkOut,
    JourneyStepStakeholderLinkCreateRequest,
    JourneyStepStakeholderLinkOut,
)

router = APIRouter(prefix="/journey-blueprint", tags=["journey-blueprint"])


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for a write. Never a body-supplied
    value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


_MESSAGES = {
    "journey_blueprint_journey_not_found": "Journey が見つかりません。",
    "journey_step_not_found": "指定された Journey Step が見つかりません。",
    "stakeholder_not_found": "対象の Stakeholder が見つかりません。",
    "value_exchange_not_found": "対象の Value Exchange が見つかりません。",
    "journey_blueprint_target_not_found": "参照先が見つかりません。",
    "journey_blueprint_validation_error": "入力値が不正です。",
}


def _reject(code: str, status_code: int, detail: str = "") -> HTTPException:
    message = _MESSAGES.get(code, detail or code)
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _raise_for_error(exc: Exception) -> None:
    if isinstance(exc, jb.JourneyNotFound):
        raise _reject("journey_blueprint_journey_not_found", 404)
    if isinstance(exc, jb.StepNotFound):
        raise _reject("journey_step_not_found", 404)
    if isinstance(exc, jb.StakeholderNotFound):
        raise _reject("stakeholder_not_found", 404)
    if isinstance(exc, jb.ExchangeNotFound):
        raise _reject("value_exchange_not_found", 404)
    if isinstance(exc, jb.NotFound):
        raise _reject("journey_blueprint_target_not_found", 404)
    if isinstance(exc, jb.ValidationError):
        raise _reject("journey_blueprint_validation_error", 422, str(exc))
    raise


@router.get("", response_model=BlueprintOut)
def get_blueprint_endpoint(
    journey_key: str = Query(...), system_id: int = Depends(get_system_id)
) -> BlueprintOut:
    with get_conn() as conn:
        try:
            result = jb.build_blueprint(conn, system_id, journey_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return BlueprintOut(**result)


@router.get("/diff", response_model=BlueprintDiffOut)
def get_blueprint_diff_endpoint(
    journey_key: str = Query(...), system_id: int = Depends(get_system_id)
) -> BlueprintDiffOut:
    with get_conn() as conn:
        try:
            result = jb.diff_as_is_to_be(conn, system_id, journey_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return BlueprintDiffOut(**result)


@router.post("/stakeholder-links", response_model=JourneyStepStakeholderLinkOut, status_code=201)
def add_stakeholder_link_endpoint(
    payload: JourneyStepStakeholderLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> JourneyStepStakeholderLinkOut:
    with get_conn() as conn:
        try:
            result = jb.add_stakeholder_link(
                conn, system_id=system_id, journey_key=payload.journey_key, step_key=payload.step_key,
                stakeholder_key=payload.stakeholder_key, role=payload.role, note=payload.note,
                created_by=_principal_actor(principal), now=time.time(),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return JourneyStepStakeholderLinkOut(**result)


@router.post("/delivery-links", response_model=JourneyStepDeliveryLinkOut, status_code=201)
def add_delivery_link_endpoint(
    payload: JourneyStepDeliveryLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> JourneyStepDeliveryLinkOut:
    with get_conn() as conn:
        try:
            result = jb.add_delivery_link(
                conn, system_id=system_id, journey_key=payload.journey_key, step_key=payload.step_key,
                delivery_kind=payload.delivery_kind, target_kind=payload.target_kind,
                target_ref=payload.target_ref, note=payload.note,
                created_by=_principal_actor(principal), now=time.time(),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return JourneyStepDeliveryLinkOut(**result)


@router.post("/exchange-links", response_model=JourneyStepExchangeLinkOut, status_code=201)
def add_exchange_link_endpoint(
    payload: JourneyStepExchangeLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> JourneyStepExchangeLinkOut:
    with get_conn() as conn:
        try:
            result = jb.add_exchange_link(
                conn, system_id=system_id, journey_key=payload.journey_key, step_key=payload.step_key,
                exchange_key=payload.exchange_key, note=payload.note,
                created_by=_principal_actor(principal), now=time.time(),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return JourneyStepExchangeLinkOut(**result)
