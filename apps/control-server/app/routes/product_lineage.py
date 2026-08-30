"""Objective Map / Gap Workbench projection routes (Issue #432, Epic #427).

`docs/product-objective-lineage.md` §10/§3 is the routing contract: these
two GETs are declared as INDEPENDENT top-level paths, never nested under
`/product-objectives/...` -- `GET /product-objectives/{objective_key}` is
already registered with a path param and would swallow
`/product-objectives/objective-map` before it ever reached this router (the
same defect #338 hit registering `/joint-understanding/lineage` under
`/joint-understanding/{ju_id}`).

Both endpoints are read-only projections composed by
`app/product_objective_projection.py`; this module contains no domain logic
of its own. `Depends(get_system_id)` only -- no write dependency, because a
GET never mutates state (§10: "GET は書き込まない").

probe-agent:
  role: API boundary for the Objective Map / Gap Workbench projections
  capability: product-objective-lineage
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify both endpoints are reachable at their own top-level path (never swallowed by /product-objectives/{objective_key}) and that neither call ever writes to the database.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import product_objective_projection
from ..auth import get_system_id
from ..db import get_conn
from ..models import GapWorkbenchOut, ObjectiveMapOut

router = APIRouter(tags=["product-objective-lineage"])


@router.get("/objective-map", response_model=ObjectiveMapOut)
def get_objective_map_endpoint(system_id: int = Depends(get_system_id)) -> ObjectiveMapOut:
    with get_conn() as conn:
        result = product_objective_projection.build_objective_map(conn, system_id)
    return ObjectiveMapOut(**result)


@router.get("/gap-workbench", response_model=GapWorkbenchOut)
def get_gap_workbench_endpoint(system_id: int = Depends(get_system_id)) -> GapWorkbenchOut:
    with get_conn() as conn:
        result = product_objective_projection.build_gap_workbench(conn, system_id)
    return GapWorkbenchOut(**result)
