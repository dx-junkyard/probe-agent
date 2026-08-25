"""Stakeholder Value Network API: Stakeholder / Need / Environment
Observation / Value Exchange (Issue #420, Epic #418).

`docs/stakeholder-value-network.md` §10 is the endpoint contract this module
implements against `app/stakeholder_network.py`'s deterministic domain
service. What this boundary deliberately does NOT do:

* It never accepts `created_by` / `decided_by` / `decision_method` /
  `authored_by_kind` from a request body -- every write request model is
  `ConfigDict(extra="forbid")` and carries none of those fields; they are
  always derived from the route path and the authenticated `Principal`
  (#337's rule, `routes/ux_design.py`'s identical rule one layer over).
* GET writes nothing -- a page view is never a confirmation (invariant 5 /
  #382's rule, applied here).

probe-agent:
  role: API boundary for Stakeholder / Need / Environment Observation / Value Exchange / reference / evidence / decision ledger
  capability: stakeholder-value-network
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify every write derives created_by/decided_by/decision_method/authored_by_kind from the authenticated Principal rather than the request body, and that GET never mutates state.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import stakeholder_network as sn
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    EnvironmentObservationCreateRequest,
    EnvironmentObservationDetailOut,
    EnvironmentObservationListOut,
    StakeholderCreateRequest,
    StakeholderDecisionCreateRequest,
    StakeholderDecisionOut,
    StakeholderDetailOut,
    StakeholderEvidenceRefCreateRequest,
    StakeholderEvidenceRefListOut,
    StakeholderEvidenceRefOut,
    StakeholderExchangeLineageOut,
    StakeholderListOut,
    StakeholderNeedCreateRequest,
    StakeholderNeedDetailOut,
    StakeholderNeedListOut,
    StakeholderNeedRevisionCreateRequest,
    StakeholderNeedRevisionListOut,
    StakeholderOut,
    StakeholderRefCreateRequest,
    StakeholderRefListOut,
    StakeholderRefOut,
    StakeholderRevisionCreateRequest,
    StakeholderRevisionListOut,
    StakeholderRoleAssignmentCreateRequest,
    StakeholderRoleAssignmentOut,
    StakeholderViewPreferenceOut,
    StakeholderViewPreferenceUpdateRequest,
    ValueExchangeCreateRequest,
    ValueExchangeDetailOut,
    ValueExchangeListOut,
    ValueExchangeRevisionCreateRequest,
    ValueExchangeRevisionListOut,
)
from ..stakeholder_network import (
    ConsiderationIncomplete,
    DecisionStaleDigest,
    ImpactKindInvalid,
    JourneyStepNotFound,
    KeyConflict,
    KeyRequired,
    NotDecidable,
    NotFound,
    RefKindInvalid,
    SelfLoop,
    StakeholderValidationError,
    SubjectNotFound,
    TargetNotFound,
    ValidityInverted,
    ValueStatementRequired,
)

router = APIRouter(prefix="/stakeholder-network", tags=["stakeholder-network"])


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for a write. Never a body-supplied
    value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


# --- Finite reject-code mapping (§10) --------------------------------------------

_MESSAGES = {
    "stakeholder_key_required": "key を指定してください。",
    "stakeholder_key_conflict": "同じ key が既にこの System に存在します。",
    "stakeholder_not_found": "対象が見つかりません。",
    "stakeholder_ref_kind_invalid": "ref_kind が不正です。",
    "stakeholder_ref_target_not_found": "参照先が見つかりません。",
    "observation_impact_kind_invalid": "impact_kind または target_ref_kind が不正です。",
    "exchange_self_loop": "provider と receiver が同じ場合は exchange_kind='information' のみ指定できます。",
    "exchange_value_statement_required": "value_statement を指定してください。",
    "exchange_consideration_incomplete": "consideration_state='present' の場合は consideration_kind と consideration_statement の両方が必要です。",
    "exchange_validity_inverted": "valid_to は valid_from より後である必要があります。",
    "stakeholder_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "stakeholder_not_decidable": "この状態からはその決定を記録できません。",
    "journey_step_not_found": "指定された Journey Step が見つかりません。",
}


def _reject(code: str, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": _MESSAGES[code]})


def _raise_for_error(exc: Exception) -> None:
    """Translate one `app.stakeholder_network` exception into the exact §10
    HTTP response. Subclasses of `NotFound` are checked before the generic
    `NotFound` fallback. Re-raises anything unrecognized."""
    if isinstance(exc, KeyRequired):
        raise _reject("stakeholder_key_required", 422)
    if isinstance(exc, KeyConflict):
        raise _reject("stakeholder_key_conflict", 409)
    if isinstance(exc, RefKindInvalid):
        raise _reject("stakeholder_ref_kind_invalid", 422)
    if isinstance(exc, ImpactKindInvalid):
        raise _reject("observation_impact_kind_invalid", 422)
    if isinstance(exc, JourneyStepNotFound):
        raise _reject("journey_step_not_found", 404)
    if isinstance(exc, TargetNotFound):
        raise _reject("stakeholder_ref_target_not_found", 404)
    if isinstance(exc, SelfLoop):
        raise _reject("exchange_self_loop", 422)
    if isinstance(exc, ValueStatementRequired):
        raise _reject("exchange_value_statement_required", 422)
    if isinstance(exc, ConsiderationIncomplete):
        raise _reject("exchange_consideration_incomplete", 422)
    if isinstance(exc, ValidityInverted):
        raise _reject("exchange_validity_inverted", 422)
    if isinstance(exc, SubjectNotFound):
        raise _reject("stakeholder_not_found", 404)
    if isinstance(exc, DecisionStaleDigest):
        raise _reject("stakeholder_decision_stale_digest", 409)
    if isinstance(exc, NotDecidable):
        raise _reject("stakeholder_not_decidable", 422)
    if isinstance(exc, NotFound):
        raise _reject("stakeholder_not_found", 404)
    if isinstance(exc, StakeholderValidationError):
        raise HTTPException(status_code=422, detail=str(exc))
    raise


# --- Stakeholders ------------------------------------------------------------------


@router.get("/stakeholders", response_model=StakeholderListOut)
def list_stakeholders_endpoint(system_id: int = Depends(get_system_id)) -> StakeholderListOut:
    with get_conn() as conn:
        result = sn.list_stakeholders(conn, system_id)
    return StakeholderListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/stakeholders", response_model=StakeholderOut, status_code=201)
def create_stakeholder_endpoint(
    payload: StakeholderCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderOut:
    with get_conn() as conn:
        try:
            detail = sn.create_stakeholder(
                conn, system_id=system_id, stakeholder_key=payload.stakeholder_key,
                display_name=payload.display_name, stakeholder_kind=payload.stakeholder_kind,
                description=payload.description, context_note=payload.context_note,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderOut(**detail)


@router.get("/stakeholders/{stakeholder_key}", response_model=StakeholderDetailOut)
def get_stakeholder_endpoint(stakeholder_key: str, system_id: int = Depends(get_system_id)) -> StakeholderDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.get_stakeholder_detail(conn, system_id, stakeholder_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderDetailOut(**detail)


@router.post(
    "/stakeholders/{stakeholder_key}/revisions", response_model=StakeholderDetailOut, status_code=201
)
def add_stakeholder_revision_endpoint(
    stakeholder_key: str,
    payload: StakeholderRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.add_stakeholder_revision(
                conn, system_id=system_id, stakeholder_key=stakeholder_key,
                display_name=payload.display_name, stakeholder_kind=payload.stakeholder_kind,
                description=payload.description, context_note=payload.context_note,
                change_note=payload.change_note, authored_by_kind="developer", decision_method="manual",
                intelligence_run_id=None, created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderDetailOut(**detail)


@router.get("/stakeholders/{stakeholder_key}/revisions", response_model=StakeholderRevisionListOut)
def list_stakeholder_revisions_endpoint(
    stakeholder_key: str, system_id: int = Depends(get_system_id)
) -> StakeholderRevisionListOut:
    with get_conn() as conn:
        try:
            result = sn.list_stakeholder_revisions(conn, system_id, stakeholder_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderRevisionListOut(
        system_id=system_id, stakeholder_key=stakeholder_key, generated_at=time.time(), **result
    )


@router.post(
    "/stakeholders/{stakeholder_key}/roles", response_model=StakeholderRoleAssignmentOut, status_code=201
)
def add_role_assignment_endpoint(
    stakeholder_key: str,
    payload: StakeholderRoleAssignmentCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderRoleAssignmentOut:
    with get_conn() as conn:
        try:
            out = sn.add_role_assignment(
                conn, system_id=system_id, stakeholder_key=stakeholder_key, role=payload.role,
                scope_kind=payload.scope_kind, scope_ref=payload.scope_ref, note=payload.note,
                decision_method="manual", created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderRoleAssignmentOut(**out)


# --- Stakeholder Needs --------------------------------------------------------------


@router.get("/needs", response_model=StakeholderNeedListOut)
def list_needs_endpoint(system_id: int = Depends(get_system_id)) -> StakeholderNeedListOut:
    with get_conn() as conn:
        result = sn.list_needs(conn, system_id)
    return StakeholderNeedListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/needs", response_model=StakeholderNeedDetailOut, status_code=201)
def create_need_endpoint(
    payload: StakeholderNeedCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderNeedDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.create_need(
                conn, system_id=system_id, need_key=payload.need_key, stakeholder_key=payload.stakeholder_key,
                need_kind=payload.need_kind, statement=payload.statement, rationale=payload.rationale,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderNeedDetailOut(**detail)


@router.get("/needs/{need_key}", response_model=StakeholderNeedDetailOut)
def get_need_endpoint(need_key: str, system_id: int = Depends(get_system_id)) -> StakeholderNeedDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.get_need_detail(conn, system_id, need_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderNeedDetailOut(**detail)


@router.post("/needs/{need_key}/revisions", response_model=StakeholderNeedDetailOut, status_code=201)
def add_need_revision_endpoint(
    need_key: str,
    payload: StakeholderNeedRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderNeedDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.add_need_revision(
                conn, system_id=system_id, need_key=need_key, need_kind=payload.need_kind,
                statement=payload.statement, rationale=payload.rationale, stakeholder_key=payload.stakeholder_key,
                change_note=payload.change_note, authored_by_kind="developer", decision_method="manual",
                intelligence_run_id=None, created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderNeedDetailOut(**detail)


@router.get("/needs/{need_key}/revisions", response_model=StakeholderNeedRevisionListOut)
def list_need_revisions_endpoint(
    need_key: str, system_id: int = Depends(get_system_id)
) -> StakeholderNeedRevisionListOut:
    with get_conn() as conn:
        try:
            result = sn.list_need_revisions(conn, system_id, need_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderNeedRevisionListOut(system_id=system_id, need_key=need_key, generated_at=time.time(), **result)


# --- Environment Observations --------------------------------------------------------


@router.get("/observations", response_model=EnvironmentObservationListOut)
def list_observations_endpoint(system_id: int = Depends(get_system_id)) -> EnvironmentObservationListOut:
    with get_conn() as conn:
        result = sn.list_observations(conn, system_id)
    return EnvironmentObservationListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/observations", response_model=EnvironmentObservationDetailOut, status_code=201)
def create_observation_endpoint(
    payload: EnvironmentObservationCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EnvironmentObservationDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.create_observation(
                conn, system_id=system_id, observation_key=payload.observation_key,
                statement=payload.statement, source_note=payload.source_note,
                observation_confidence=payload.observation_confidence, observed_at=payload.observed_at,
                supersedes_observation_key=payload.supersedes_observation_key,
                impacts=[i.model_dump() for i in payload.impacts], authored_by_kind="developer",
                decision_method="manual", intelligence_run_id=None, created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return EnvironmentObservationDetailOut(**detail)


@router.get("/observations/{observation_key}", response_model=EnvironmentObservationDetailOut)
def get_observation_endpoint(
    observation_key: str, system_id: int = Depends(get_system_id)
) -> EnvironmentObservationDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.get_observation_detail(conn, system_id, observation_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return EnvironmentObservationDetailOut(**detail)


# --- Value Exchanges ------------------------------------------------------------------


@router.get("/exchanges", response_model=ValueExchangeListOut)
def list_exchanges_endpoint(system_id: int = Depends(get_system_id)) -> ValueExchangeListOut:
    with get_conn() as conn:
        result = sn.list_exchanges(conn, system_id)
    return ValueExchangeListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/exchanges", response_model=ValueExchangeDetailOut, status_code=201)
def create_exchange_endpoint(
    payload: ValueExchangeCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ValueExchangeDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.create_exchange(
                conn, system_id=system_id, exchange_key=payload.exchange_key,
                provider_stakeholder_key=payload.provider_stakeholder_key,
                receiver_stakeholder_key=payload.receiver_stakeholder_key,
                exchange_kind=payload.exchange_kind, value_statement=payload.value_statement,
                consideration_state=payload.consideration_state, consideration_kind=payload.consideration_kind,
                consideration_statement=payload.consideration_statement, channel=payload.channel,
                trigger=payload.trigger, cadence=payload.cadence, valid_from=payload.valid_from,
                valid_to=payload.valid_to, created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ValueExchangeDetailOut(**detail)


@router.get("/exchanges/{exchange_key}", response_model=ValueExchangeDetailOut)
def get_exchange_endpoint(exchange_key: str, system_id: int = Depends(get_system_id)) -> ValueExchangeDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.get_exchange_detail(conn, system_id, exchange_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ValueExchangeDetailOut(**detail)


@router.post("/exchanges/{exchange_key}/revisions", response_model=ValueExchangeDetailOut, status_code=201)
def add_exchange_revision_endpoint(
    exchange_key: str,
    payload: ValueExchangeRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ValueExchangeDetailOut:
    with get_conn() as conn:
        try:
            detail = sn.add_exchange_revision(
                conn, system_id=system_id, exchange_key=exchange_key,
                provider_stakeholder_key=payload.provider_stakeholder_key,
                receiver_stakeholder_key=payload.receiver_stakeholder_key,
                exchange_kind=payload.exchange_kind, value_statement=payload.value_statement,
                consideration_state=payload.consideration_state, consideration_kind=payload.consideration_kind,
                consideration_statement=payload.consideration_statement, channel=payload.channel,
                trigger=payload.trigger, cadence=payload.cadence, valid_from=payload.valid_from,
                valid_to=payload.valid_to, change_note=payload.change_note, authored_by_kind="developer",
                decision_method="manual", intelligence_run_id=None, created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ValueExchangeDetailOut(**detail)


@router.get("/exchanges/{exchange_key}/revisions", response_model=ValueExchangeRevisionListOut)
def list_exchange_revisions_endpoint(
    exchange_key: str, system_id: int = Depends(get_system_id)
) -> ValueExchangeRevisionListOut:
    with get_conn() as conn:
        try:
            result = sn.list_exchange_revisions(conn, system_id, exchange_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ValueExchangeRevisionListOut(
        system_id=system_id, exchange_key=exchange_key, generated_at=time.time(), **result
    )


@router.get("/exchanges/{exchange_key}/lineage", response_model=StakeholderExchangeLineageOut)
def get_exchange_lineage_endpoint(
    exchange_key: str, system_id: int = Depends(get_system_id)
) -> StakeholderExchangeLineageOut:
    """§7.1: read-only, deterministic, writes nothing. See
    `app.stakeholder_network.get_exchange_lineage`'s docstring for the
    guarded-loader-per-section discipline."""
    with get_conn() as conn:
        try:
            result = sn.get_exchange_lineage(conn, system_id, exchange_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderExchangeLineageOut(system_id=system_id, generated_at=time.time(), **result)


# --- References + evidence + decisions -----------------------------------------------


@router.post("/refs", response_model=StakeholderRefOut, status_code=201)
def create_ref_endpoint(
    payload: StakeholderRefCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderRefOut:
    with get_conn() as conn:
        try:
            out = sn.create_ref(
                conn, system_id=system_id, source_kind=payload.source_kind, source_key=payload.source_key,
                ref_kind=payload.ref_kind, target_ref=payload.target_ref, note=payload.note,
                decision_method="manual", created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderRefOut(**out)


@router.get("/refs", response_model=StakeholderRefListOut)
def list_refs_endpoint(
    source_kind: Optional[str] = Query(default=None),
    source_key: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> StakeholderRefListOut:
    with get_conn() as conn:
        result = sn.list_refs(conn, system_id, source_kind=source_kind, source_key=source_key)
    return StakeholderRefListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/evidence-refs", response_model=StakeholderEvidenceRefOut, status_code=201)
def create_evidence_ref_endpoint(
    payload: StakeholderEvidenceRefCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderEvidenceRefOut:
    with get_conn() as conn:
        try:
            out = sn.create_evidence_ref(
                conn, system_id=system_id, subject_kind=payload.subject_kind, subject_key=payload.subject_key,
                evidence_kind=payload.evidence_kind, evidence_ref=payload.evidence_ref,
                statement=payload.statement, created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderEvidenceRefOut(**out)


@router.get("/evidence-refs", response_model=StakeholderEvidenceRefListOut)
def list_evidence_refs_endpoint(
    subject_kind: str = Query(...),
    subject_key: str = Query(...),
    system_id: int = Depends(get_system_id),
) -> StakeholderEvidenceRefListOut:
    with get_conn() as conn:
        result = sn.list_evidence_refs(conn, system_id, subject_kind, subject_key)
    return StakeholderEvidenceRefListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/decisions", response_model=StakeholderDecisionOut, status_code=201)
def record_decision_endpoint(
    payload: StakeholderDecisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderDecisionOut:
    with get_conn() as conn:
        try:
            row = sn.record_decision(
                conn, system_id=system_id, subject_kind=payload.subject_kind, subject_key=payload.subject_key,
                decision=payload.decision, rationale=payload.rationale, captured_digest=payload.captured_digest,
                decided_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return StakeholderDecisionOut(**row)


# --- §12. View preference (display settings only) -------------------------------------


@router.get("/view-preference", response_model=StakeholderViewPreferenceOut)
def get_view_preference_endpoint(
    system_id: int = Depends(get_system_id), principal: Principal = Depends(require_user)
) -> StakeholderViewPreferenceOut:
    with get_conn() as conn:
        out = sn.get_view_preference(conn, system_id, _principal_actor(principal))
    return StakeholderViewPreferenceOut(**out)


@router.put("/view-preference", response_model=StakeholderViewPreferenceOut)
def update_view_preference_endpoint(
    payload: StakeholderViewPreferenceUpdateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StakeholderViewPreferenceOut:
    with get_conn() as conn:
        out = sn.update_view_preference(
            conn, system_id, _principal_actor(principal), active_view=payload.active_view,
            filters=payload.filters, collapsed_refs=payload.collapsed_refs, pinned_refs=payload.pinned_refs,
        )
    return StakeholderViewPreferenceOut(**out)
