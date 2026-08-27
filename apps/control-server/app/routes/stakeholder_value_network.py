"""Stakeholder Value Network API (Issue #422, Epic #418).

`docs/stakeholder-value-network.md` §7.1/§10 is the endpoint contract. This
is a single read-only endpoint over `app.stakeholder_value_network.
build_value_network`'s deterministic projection -- it writes nothing (GET
never mutates state, invariant 9 / #382's rule), calls no LLM, and accepts
no request body.

probe-agent:
  role: API boundary for the read-only Stakeholder Value Network projection
  capability: stakeholder-value-network
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read]
  probe_value: Verify this endpoint never writes and that its response matches app.stakeholder_value_network.build_value_network's output exactly.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .. import stakeholder_value_network as svn
from ..auth import get_system_id
from ..db import get_conn
from ..models import ValueNetworkOut

router = APIRouter(tags=["stakeholder-value-network"])


@router.get("/stakeholder-value-network", response_model=ValueNetworkOut)
def get_value_network_endpoint(system_id: int = Depends(get_system_id)) -> ValueNetworkOut:
    """§7.1: read-only, deterministic, writes nothing. See
    `app.stakeholder_value_network.build_value_network`'s docstring for the
    guarded-loader-per-section discipline."""
    with get_conn() as conn:
        result = svn.build_value_network(conn, system_id)
    return ValueNetworkOut(system_id=system_id, generated_at=time.time(), **result)
