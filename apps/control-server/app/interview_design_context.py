"""Bounded read-only design facts shared by interview and screen dialogue."""
from __future__ import annotations

import json
import sqlite3

from . import product_feature, product_objective, ux_design


def build_interview_design_context(conn: sqlite3.Connection, system_id: int) -> dict:
    # Read identities with a SQL limit, then let the owning services resolve
    # revisions, decisions and explicit links. Never infer links by title.
    sections = (
        ("objectives", "product_objective", "objective_key", product_objective.get_objective_detail),
        ("milestones", "product_milestone", "milestone_key", product_objective.get_milestone_detail),
        ("gaps", "product_gap", "gap_key", product_objective.get_gap_detail),
        ("journeys", "ux_journey", "journey_key", ux_design.get_journey_detail),
        ("requirements", "ux_requirement", "requirement_key", ux_design.get_requirement_detail),
        ("features", "product_feature", "feature_key", product_feature.get_feature_detail),
    )
    result = {"scope": "current_system_design_not_pinned_premise", "sections": {}}
    for name, table, key, reader in sections:
        section = {"items": [], "total": None, "omitted": None, "state": "available"}
        result["sections"][name] = section
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE system_id = ?", (system_id,),
            ).fetchone()[0]
            section["total"] = total
            rows = conn.execute(
                f"SELECT {key} FROM {table} WHERE system_id = ? ORDER BY id DESC LIMIT 5",
                (system_id,),
            ).fetchall()
            for row in rows:
                item = reader(conn, system_id, row[key])
                # Preserve valid JSON and complete references; never cut a
                # serialized entity mid-field. Each layer has its own budget.
                candidate = section["items"] + [item]
                if len(json.dumps(candidate, ensure_ascii=False)) > 6000:
                    continue
                section["items"].append(item)
                if item.get("degraded_sections"):
                    section["state"] = "degraded"
            section["omitted"] = total - len(section["items"])
        except (sqlite3.Error, ValueError):
            section["state"] = "unavailable"
    return result
