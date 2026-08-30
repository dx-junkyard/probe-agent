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


def build_screen_discussion_context(
    screen_id: str, system_id: int, route_params: Optional[Dict[str, str]] = None
) -> Optional[ScreenDiscussionContext]:
    """Return canonical facts only for discussion-enabled screens."""
    params = route_params or {}
    if screen_id == "overview":
        return _overview_context(system_id)
    if screen_id == "interview":
        return _interview_context(system_id, params)
    if screen_id == "ux-design-studio":
        return _ux_design_context(system_id, params)
    if screen_id == "journey-blueprint":
        return _journey_blueprint_context(system_id, params)
    return None
