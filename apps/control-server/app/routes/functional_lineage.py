"""Functional Lineage View API (Issue #424, Epic #418).

`docs/stakeholder-value-network.md` §9/§10 is the endpoint contract. This is
a single read-only endpoint over `app.functional_lineage.
build_functional_lineage`'s deterministic projection -- it writes nothing
(GET never mutates state, invariant 9 / #382's rule), calls no LLM, and
accepts no request body.

probe-agent:
  role: API boundary for the read-only Functional Lineage + Gap/Impact Overlay projection
  capability: functional-lineage
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read]
  probe_value: Verify this endpoint never writes and that its response matches app.functional_lineage.build_functional_lineage's output exactly.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .. import functional_lineage as fl
from ..auth import get_system_id
from ..db import get_conn
from ..models import FunctionalLineageOut

router = APIRouter(tags=["functional-lineage"])


@router.get("/functional-lineage", response_model=FunctionalLineageOut)
def get_functional_lineage_endpoint(system_id: int = Depends(get_system_id)) -> FunctionalLineageOut:
    """§9: read-only, deterministic, writes nothing. See
    `app.functional_lineage.build_functional_lineage`'s docstring for the
    guarded-loader-per-section discipline."""
    with get_conn() as conn:
        result = fl.build_functional_lineage(conn, system_id)
    return FunctionalLineageOut(system_id=system_id, generated_at=time.time(), **result)
