"""Read-only, screen-specific facts for assistant discussions.

The assistant does not create a second understanding/design model.  Each
provider below reads the same canonical projection/domain service as the
corresponding Dashboard screen and returns a bounded JSON context pack.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .db import get_conn


MAX_LIST_ITEMS = 50


@dataclass(frozen=True)
class ScreenDiscussionContext:
    facts: Dict[str, Any]
    sources: List[Dict[str, str]]
    # Issue #456: the finite `DiscussionOperationResult` (`app/models.py`)
    # this READ attempt reports, kept as its OWN field -- never folded into
    # `facts`. Every existing constructor call below builds a SUCCESSFUL
    # context and leaves this at its default, so the field is additive.
    # `build_screen_discussion_context` is the only place that produces the
    # other values, on a caught exception (§1.3: a diagnostics-gathering
    # failure must degrade, never take down the whole `/assistant/ask`
    # request -- see its own docstring).
    operation_state: str = "available"  # DiscussionOperationResult
    reason: str = ""


def _selected_or_none(loader) -> tuple[Optional[Dict[str, Any]], bool]:
    """A stale/deleted deep link must not make the whole assistant fail."""
    try:
        return loader(), False
    except ValueError:
        return None, True


def _positive_int(value: Optional[str]) -> Optional[int]:
    try:
        parsed = int(value or "")
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _overview_context(system_id: int) -> ScreenDiscussionContext:
    from .overview_projection import build_overview

    overview = build_overview(system_id)
    facts = {
        "snapshot_id": overview.snapshot_id,
        "snapshot_commit_sha": overview.snapshot_commit_sha,
        "snapshot_freshness": overview.snapshot_freshness,
        "understanding_revision_id": overview.understanding_revision_id,
        "understanding_confirmed_at": overview.understanding_confirmed_at,
        "system_brief": asdict(overview.brief) if overview.brief is not None else None,
        "findings": [asdict(item) for item in overview.findings[:20]],
        "next_action": asdict(overview.next_action) if overview.next_action is not None else None,
        "degraded_sections": list(overview.degraded_sections),
    }
    return ScreenDiscussionContext(
        facts=facts,
        sources=[{"id": "overview", "title": "Canonical Overview projection"}],
    )


def _interview_context(
    system_id: int, route_params: Dict[str, str]
) -> ScreenDiscussionContext:
    from .understanding_brief import build_understanding_brief

    requested_session_id = _positive_int(route_params.get("session"))
    with get_conn() as conn:
        sessions = conn.execute(
            """SELECT id, snapshot_id, title, focus, status, stage, updated_at
               FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 20""",
            (system_id,),
        ).fetchall()
        session = None
        if requested_session_id is not None:
            session = conn.execute(
                "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
                (requested_session_id, system_id),
            ).fetchone()
        brief = build_understanding_brief(
            conn, system_id, session["id"] if session is not None else None
        )
        selected = None
        if session is not None:
            selected = {
                "id": session["id"],
                "snapshot_id": session["snapshot_id"],
                "title": session["title"],
                "focus": session["focus"],
                "status": session["status"],
                "stage": session["stage"],
                "current_understanding": (
                    json.loads(session["current_understanding"])
                    if session["current_understanding"] else None
                ),
                "gap_analysis": (
                    json.loads(session["gap_analysis"])
                    if session["gap_analysis"] else None
                ),
                "open_questions": (
                    json.loads(session["open_questions"])
                    if session["open_questions"] else None
                ),
                "user_intent": session["user_intent"],
                "understanding_confirmed_at": session["understanding_confirmed_at"],
            }
    sources = [{"id": "interview_sessions", "title": "Interview session list"}]
    if selected is not None:
        sources.extend([
            {"id": f"interview_session:{selected['id']}", "title": "Selected Interview session"},
            {"id": "understanding_brief", "title": "Canonical Understanding Brief"},
        ])
    return ScreenDiscussionContext(
        facts={
            "requested_session_id": requested_session_id,
            "selected_session": selected,
            "understanding_brief": asdict(brief),
            "available_sessions": [dict(row) for row in sessions],
        },
        sources=sources,
    )


def _ux_design_context(
    system_id: int, route_params: Dict[str, str]
) -> ScreenDiscussionContext:
    from . import solution_design, ux_design

    journey_key = (route_params.get("journey") or "").strip() or None
    requirement_key = (route_params.get("requirement") or "").strip() or None
    design_key = (route_params.get("design") or "").strip() or None
    with get_conn() as conn:
        journeys = ux_design.list_journeys(conn, system_id)
        requirements = ux_design.list_requirements(conn, system_id)
        designs = solution_design.list_designs(conn, system_id=system_id)
        selected_journey, journey_missing = _selected_or_none(
            lambda: ux_design.get_journey_detail(conn, system_id, journey_key)
        ) if journey_key else (None, False)
        selected_requirement, requirement_missing = _selected_or_none(
            lambda: ux_design.get_requirement_detail(conn, system_id, requirement_key)
        ) if requirement_key else (None, False)
        selected_design, design_missing = _selected_or_none(
            lambda: solution_design.get_design_detail(
                conn, system_id=system_id, design_key=design_key
            )
        ) if design_key else (None, False)
    sources = [
        {"id": "ux_journeys", "title": "Canonical UX Journeys"},
        {"id": "ux_requirements", "title": "Canonical UX Requirements"},
        {"id": "solution_designs", "title": "Canonical Solution Designs"},
    ]
    for kind, key in (
        ("ux_journey", journey_key),
        ("ux_requirement", requirement_key),
        ("solution_design", design_key),
    ):
        if key:
            sources.append({"id": f"{kind}:{key}", "title": f"Selected {kind}"})
    return ScreenDiscussionContext(
        facts={
            "active_tab": route_params.get("tab", "journeys"),
            "journeys": journeys["journeys"][:MAX_LIST_ITEMS],
            "requirements": requirements["requirements"][:MAX_LIST_ITEMS],
            "solution_designs": designs["designs"][:MAX_LIST_ITEMS],
            "selected_journey": selected_journey,
            "selected_requirement": selected_requirement,
            "selected_solution_design": selected_design,
            "selection_not_found": {
                "journey": journey_missing,
                "requirement": requirement_missing,
                "solution_design": design_missing,
            },
            "degraded_sections": sorted(set(
                journeys["degraded_sections"]
                + requirements["degraded_sections"]
                + designs["degraded_sections"]
            )),
        },
        sources=sources,
    )


def _journey_blueprint_context(
    system_id: int, route_params: Dict[str, str]
) -> ScreenDiscussionContext:
    from . import journey_blueprint, ux_design

    journey_key = (route_params.get("journey") or "").strip() or None
    with get_conn() as conn:
        journeys = ux_design.list_journeys(conn, system_id)
        blueprint, journey_missing = _selected_or_none(
            lambda: journey_blueprint.build_blueprint(conn, system_id, journey_key)
        ) if journey_key else (None, False)
        diff = None
        diff_missing = False
        if journey_key and blueprint is not None and route_params.get("view") == "diff":
            diff, diff_missing = _selected_or_none(
                lambda: journey_blueprint.diff_as_is_to_be(conn, system_id, journey_key)
            )
    sources = [{"id": "ux_journeys", "title": "Canonical UX Journeys"}]
    if journey_key:
        sources.append({
            "id": f"journey_blueprint:{journey_key}",
            "title": "Canonical Journey Service Blueprint",
        })
        if diff is not None:
            sources.append({
                "id": f"journey_blueprint_diff:{journey_key}",
                "title": "Canonical as-is/to-be Blueprint diff",
            })
    return ScreenDiscussionContext(
        facts={
            "view": route_params.get("view", "blueprint"),
            "selected_journey_key": journey_key,
            "available_journeys": journeys["journeys"][:MAX_LIST_ITEMS],
            "blueprint": blueprint,
            "diff": diff,
            "selection_not_found": journey_missing,
            "diff_unavailable": diff_missing,
        },
        sources=sources,
    )


def _objective_map_context(
    system_id: int, route_params: Dict[str, str]
) -> ScreenDiscussionContext:
    """Issue #453 (Epic #443 Phase 4). Reads the SAME canonical projection
    `objective-map.tsx` itself renders (`product_objective_projection.
    build_objective_map`) plus the selected Objective/Milestone/Gap detail --
    never a second Objective Map model. `view`/`objective`/`milestone`/`gap`
    are the exact query params `objectiveMapSelectionFromSearchParams`
    (Dashboard) already reads (§4.1's live-selection contract)."""
    from . import product_objective, product_objective_projection

    view = (route_params.get("view") or "objectives").strip()
    objective_key = (route_params.get("objective") or "").strip() or None
    milestone_key = (route_params.get("milestone") or "").strip() or None
    gap_key = (route_params.get("gap") or "").strip() or None
    with get_conn() as conn:
        obj_map = product_objective_projection.build_objective_map(conn, system_id)
        selected_objective, objective_missing = _selected_or_none(
            lambda: product_objective.get_objective_detail(conn, system_id, objective_key)
        ) if objective_key else (None, False)
        selected_milestone, milestone_missing = _selected_or_none(
            lambda: product_objective.get_milestone_detail(conn, system_id, milestone_key)
        ) if milestone_key else (None, False)
        selected_gap, gap_missing = _selected_or_none(
            lambda: product_objective.get_gap_detail(conn, system_id, gap_key)
        ) if gap_key else (None, False)
    sources = [{"id": "product_objective_map", "title": "Canonical Objective Map"}]
    for kind, key in (
        ("product_objective", objective_key),
        ("product_milestone", milestone_key),
        ("product_gap", gap_key),
    ):
        if key:
            sources.append({"id": f"{kind}:{key}", "title": f"Selected {kind}"})
    return ScreenDiscussionContext(
        facts={
            "view": view,
            "objectives": obj_map.get("nodes", [])[:MAX_LIST_ITEMS],
            "selected_objective": selected_objective,
            "selected_milestone": selected_milestone,
            "selected_gap": selected_gap,
            "selection_not_found": {
                "objective": objective_missing,
                "milestone": milestone_missing,
                "gap": gap_missing,
            },
            "degraded_sections": list(obj_map.get("degraded_sections", [])),
        },
        sources=sources,
    )


def _stakeholder_value_network_context(
    system_id: int, route_params: Dict[str, str]
) -> ScreenDiscussionContext:
    """Issue #453. Reads `stakeholder_value_network.build_value_network` --
    the same projection `stakeholder-value-network.tsx` renders -- plus the
    selected `node`/`edge` detail, the exact params that page's own
    `searchParams.get("node")`/`get("edge")` already use."""
    from . import stakeholder_network as sn
    from .stakeholder_value_network import build_value_network

    node_key = (route_params.get("node") or "").strip() or None
    edge_key = (route_params.get("edge") or "").strip() or None
    with get_conn() as conn:
        network = build_value_network(conn, system_id)
        selected_node, node_missing = _selected_or_none(
            lambda: sn.get_stakeholder_detail(conn, system_id, node_key)
        ) if node_key else (None, False)
        selected_edge, edge_missing = _selected_or_none(
            lambda: sn.get_exchange_detail(conn, system_id, edge_key)
        ) if edge_key else (None, False)
    sources = [{"id": "stakeholder_value_network", "title": "Canonical Stakeholder Value Network"}]
    for kind, key in (("stakeholder", node_key), ("value_exchange", edge_key)):
        if key:
            sources.append({"id": f"{kind}:{key}", "title": f"Selected {kind}"})
    return ScreenDiscussionContext(
        facts={
            "nodes": network.get("nodes", [])[:MAX_LIST_ITEMS],
            "edges": network.get("edges", [])[:MAX_LIST_ITEMS],
            "notices": network.get("notices", [])[:MAX_LIST_ITEMS],
            "selected_stakeholder": selected_node,
            "selected_exchange": selected_edge,
            "selection_not_found": {"stakeholder": node_missing, "value_exchange": edge_missing},
            "degraded_sections": list(network.get("degraded_sections", [])),
        },
        sources=sources,
    )


# `capability-map` deliberately has NO entry here (Issue #453): none of the
# Vision-to-Feature kinds render on that screen (it shows the older #56/#57
# AST-derived Capability Hierarchy, a different model this Issue does not
# touch), so a whole-screen conversation there degrades to `unsupported`
# canonical context -- the same honest, already-established state
# `understanding_claim`/`overview_finding` have on every screen that is not
# in their own `screen_ids`.
_SCREEN_CONTEXT_PROVIDERS: Dict[str, Any] = {
    "overview": lambda system_id, params: _overview_context(system_id),
    "interview": _interview_context,
    "ux-design-studio": _ux_design_context,
    "journey-blueprint": _journey_blueprint_context,
    "objective-map": _objective_map_context,
    "stakeholder-value-network": _stakeholder_value_network_context,
}


def build_screen_discussion_context(
    screen_id: str, system_id: int, route_params: Optional[Dict[str, str]] = None
) -> Optional[ScreenDiscussionContext]:
    """Return canonical facts only for discussion-enabled screens.

    `None` means `screen_id` is not one of the discussion-enabled screens at
    all -- `unsupported` at the screen level, unchanged from before #456 (the
    caller already treats `None` as "no screen_data" and this is not the bug
    #456 fixes).

    A REGISTERED screen's provider used to run with no safety net: an
    exception inside `_overview_context` / `_interview_context` / ... (e.g. a
    guarded canonical projection raising) propagated all the way out of
    `POST /assistant/ask` as a 500, destroying the whole assistant turn over
    one screen's context read (§1.3: "診断取得失敗で会話全体を不要に壊さ
    ない"). It now degrades to an `unavailable` context with no facts/
    sources instead -- the assistant still answers, just without this
    screen's canonical facts for this turn.
    """
    params = route_params or {}
    provider = _SCREEN_CONTEXT_PROVIDERS.get(screen_id)
    if provider is None:
        return None
    try:
        return provider(system_id, params)
    except Exception:
        # Exercised directly by `tests/test_discussion_operation_result.py`'s
        # `TestScreenDiscussionContextDegrades` and by `tests/test_assistant.
        # py`'s `test_a_failing_screen_context_provider_does_not_break_the_
        # whole_ask` (which also proves `/assistant/ask` still returns 200)
        # -- not `pragma: no cover`.
        return ScreenDiscussionContext(
            facts={}, sources=[], operation_state="unavailable",
            reason="screen_discussion_context_provider_error",
        )
