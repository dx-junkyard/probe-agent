"""Connectivity status for the caller's system (Issues #165, #370).

Deterministic, LLM-free signal-reception facts backing the Dashboard's
connectivity warning badge and the setup-guide page. This endpoint never
infers *why* nothing arrived — it only reports what was observed.

Two independent finite axes are returned, and conflating them is the bug
Issue #370 fixed:

``state``      a cumulative lifecycle milestone (``no_signal`` / ``smoke_only``
               / ``receiving``). "This system has connected at least once."
               It never regresses, because a past event cannot un-happen.
``freshness``  the live operational reading (``never_received`` /
               ``receiving_now`` / ``delayed`` / ``stale``). "Is it receiving
               now." It regresses as soon as traffic stops.

The audit found a system whose last trace was ~14 days old still showing a
green 受信中, because a single cumulative trace made ``state`` ``receiving``
forever. Both axes are now returned so no surface has to pick one and hope.
"""

import time

from fastapi import APIRouter, Depends, HTTPException

from .. import state_facts
from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    SMOKE_CHECK_COMPONENT_ID,
    ConnectivityFreshnessPolicyOut,
    ConnectivityFreshnessPolicyUpdate,
    ConnectivityStatusOut,
)

router = APIRouter()


@router.get("/connectivity/status", response_model=ConnectivityStatusOut)
def get_connectivity_status(
    system_id: int = Depends(get_system_id),
) -> ConnectivityStatusOut:
    now = time.time()
    with get_conn() as conn:
        facts = state_facts.get_connectivity_facts(
            conn, system_id, SMOKE_CHECK_COMPONENT_ID, now=now
        )
        delayed_after, stale_after = state_facts.get_freshness_thresholds(
            conn, system_id
        )
        customized = (
            delayed_after,
            stale_after,
        ) != (
            state_facts.DEFAULT_DELAYED_AFTER_SECONDS,
            state_facts.DEFAULT_STALE_AFTER_SECONDS,
        )

    state = state_facts.classify_connectivity_state(
        real_trace_count=facts.real_trace_count,
        smoke_trace_count=facts.smoke_trace_count,
    )
    freshness = state_facts.classify_connectivity_freshness(
        last_trace_at=facts.last_trace_at,
        now=now,
        delayed_after_seconds=delayed_after,
        stale_after_seconds=stale_after,
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
        freshness=freshness,
        seconds_since_last_trace=(
            None if facts.last_trace_at is None else now - facts.last_trace_at
        ),
        evaluated_at=now,
        clock_skew_seconds=facts.clock_skew_seconds,
        real_trace_count_5m=facts.real_trace_count_5m,
        real_trace_count_1h=facts.real_trace_count_1h,
        real_trace_count_24h=facts.real_trace_count_24h,
        delayed_after_seconds=delayed_after,
        stale_after_seconds=stale_after,
        thresholds_customized=customized,
    )


@router.get(
    "/connectivity/freshness-policy", response_model=ConnectivityFreshnessPolicyOut
)
def get_freshness_policy(
    system_id: int = Depends(get_system_id),
) -> ConnectivityFreshnessPolicyOut:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT delayed_after_seconds, stale_after_seconds, updated_at
                 FROM connectivity_freshness_policy WHERE system_id = ?""",
            (system_id,),
        ).fetchone()
    if row is None:
        return ConnectivityFreshnessPolicyOut(
            system_id=system_id,
            delayed_after_seconds=state_facts.DEFAULT_DELAYED_AFTER_SECONDS,
            stale_after_seconds=state_facts.DEFAULT_STALE_AFTER_SECONDS,
            customized=False,
        )
    return ConnectivityFreshnessPolicyOut(
        system_id=system_id,
        delayed_after_seconds=row["delayed_after_seconds"],
        stale_after_seconds=row["stale_after_seconds"],
        customized=True,
        updated_at=row["updated_at"],
    )


@router.put(
    "/connectivity/freshness-policy", response_model=ConnectivityFreshnessPolicyOut
)
def set_freshness_policy(
    payload: ConnectivityFreshnessPolicyUpdate,
    system_id: int = Depends(get_system_id),
) -> ConnectivityFreshnessPolicyOut:
    if payload.delayed_after_seconds >= payload.stale_after_seconds:
        # Otherwise `delayed` is unreachable: the stale branch is evaluated
        # first, so a system would jump straight from receiving to stale.
        raise HTTPException(
            status_code=422,
            detail="delayed_after_seconds must be smaller than stale_after_seconds",
        )
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO connectivity_freshness_policy
                   (system_id, delayed_after_seconds, stale_after_seconds, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(system_id) DO UPDATE SET
                   delayed_after_seconds = excluded.delayed_after_seconds,
                   stale_after_seconds = excluded.stale_after_seconds,
                   updated_at = excluded.updated_at""",
            (
                system_id,
                payload.delayed_after_seconds,
                payload.stale_after_seconds,
                now,
            ),
        )
    return ConnectivityFreshnessPolicyOut(
        system_id=system_id,
        delayed_after_seconds=payload.delayed_after_seconds,
        stale_after_seconds=payload.stale_after_seconds,
        customized=True,
        updated_at=now,
    )
