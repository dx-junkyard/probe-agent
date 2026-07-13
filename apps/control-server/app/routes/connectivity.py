"""Connectivity status for the caller's system (Issue #165).

Deterministic, LLM-free signal-reception facts backing the Dashboard's
connectivity warning badge and the setup-guide page. The state vocabulary is
a finite set (``no_signal`` / ``smoke_only`` / ``receiving``); smoke traces
are recognized only by exact ``component_id`` match against the documented
``probe-smoke-check`` convention. This endpoint never infers *why* nothing
arrived — it only reports what was observed.
"""

from fastapi import APIRouter, Depends

from .. import state_facts
from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    SMOKE_CHECK_COMPONENT_ID,
    ConnectivityStatusOut,
)

router = APIRouter()


@router.get("/connectivity/status", response_model=ConnectivityStatusOut)
def get_connectivity_status(
    system_id: int = Depends(get_system_id),
) -> ConnectivityStatusOut:
    with get_conn() as conn:
        facts = state_facts.get_connectivity_facts(conn, system_id, SMOKE_CHECK_COMPONENT_ID)

    state = state_facts.classify_connectivity_state(
        real_trace_count=facts.real_trace_count,
        smoke_trace_count=facts.smoke_trace_count,
    )

    return ConnectivityStatusOut(
        system_id=system_id,
        state=state,
        total_trace_count=facts.total_trace_count,
        smoke_trace_count=facts.smoke_trace_count,
        real_trace_count=facts.real_trace_count,
        first_trace_at=facts.first_trace_at,
        last_trace_at=facts.last_trace_at,
        last_trace_component_id=facts.last_trace_component_id,
        materialized_session_ids=facts.materialized_session_ids,
    )
