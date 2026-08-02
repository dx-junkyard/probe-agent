"""Understanding Brief + Decision Readiness API (Issues #351-#354).

One read-only endpoint. `GET /interview/understanding-brief` is the only
place the Dashboard learns what the AI thinks the system is for (Vision /
System Purpose / Core Capabilities), how settled each claim is, where each
claim came from, and whether the current understanding can be used to keep
going.

Why the derivation lives on the server rather than in the Dashboard: the same
reason Issue #349 moved the workflow state here. A readiness verdict computed
from client state disappears on reload, and two screens (or a screen and a
test) can then disagree about whether the same persisted facts mean 「進行可能」
or 「要確認」.

What this endpoint deliberately does NOT do:

* It does not decide the workflow state. `GET /interview/workflow-state` stays
  the single source of `W0-A`..`W7` and of the one primary action; Readiness
  is a second axis, and the Dashboard must never substitute one for the other.
* It does not gate. Nothing here removes or disables an action -- 「進行不可」
  explains, it does not forbid (#353's 非目標).
* It writes nothing. No confirmation, no proposal decision, no policy.

probe-agent:
  role: API boundary for the Understanding Brief and Decision Readiness
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify the Brief never presents an unconfirmed claim as established, that Readiness is reproducible after a reload, and that the endpoint is System-scoped.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..auth import get_system_id
from ..db import get_conn
from ..models import UnderstandingBriefOut
from ..understanding_brief import build_understanding_brief

router = APIRouter()


@router.get("/interview/understanding-brief", response_model=UnderstandingBriefOut)
def get_understanding_brief(
    session_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> UnderstandingBriefOut:
    """Derive the Brief and Readiness for one session, or for none.

    `session_id` is optional for the same reason it is on the workflow-state
    endpoint: `W0-A` / `W0-B` exist before any session does, and the screen
    still has to say honestly 「まだ理解を構築していません」 instead of
    rendering a placeholder Vision. A `session_id` belonging to another System
    is treated exactly like none selected, so the Brief can never leak a claim
    across Systems.
    """
    with get_conn() as conn:
        result = build_understanding_brief(conn, system_id, session_id)
    return UnderstandingBriefOut(system_id=system_id, **asdict(result))
