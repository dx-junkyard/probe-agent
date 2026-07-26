"""Issue #312: canonical capability identity and many-to-many composition."""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.capability_graph import (
    CapabilityGraphError,
    confirm_capability_graph,
    graph_change_sets,
)
from app.db import SCHEMA


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    now = 1.0
    conn.execute(
        """INSERT INTO systems
            (id, name, environment, description, created_at, updated_at)
           VALUES (1, 'A', 'test', '', ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO repository_snapshots
            (id, system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (1, 1, '/tmp/repo', 'abc', 'ready', ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO interview_session
            (id, system_id, snapshot_id, title, focus, status, stage,
             created_at, updated_at)
           VALUES (1, 1, 1, '', '', 'open', 'purpose_confirmation', ?, ?)""",
        (now, now),
    )
    return conn


def _understanding(
    *,
    a_name: str = "Core A",
    a_summary: str = "A summary",
    a_children=None,
    b_children=None,
):
    return {
        "core_capabilities": [
            {
                "name": a_name,
                "summary": a_summary,
                "children": ["Shared"] if a_children is None else a_children,
            },
            {
                "name": "Core B",
                "summary": "B summary",
                "children": ["Shared"] if b_children is None else b_children,
            },
        ],
        "capability_elements": [
            {"name": "Shared", "summary": "shared function", "children": []},
        ],
        "supporting_elements": [],
        "api_boundaries": [],
    }


def _revision(conn, revision_id: int, understanding) -> None:
    conn.execute(
        """INSERT INTO understanding_revision
            (id, session_id, system_id, snapshot_id, intelligence_run_id,
             current_understanding, gap_analysis, created_at)
           VALUES (?, 1, 1, 1, NULL, ?, NULL, ?)""",
        (revision_id, json.dumps(understanding), float(revision_id)),
    )


def _confirm(
    conn,
    revision_id: int,
    understanding,
    *,
    bindings=(),
):
    _revision(conn, revision_id, understanding)
    return confirm_capability_graph(
        conn,
        system_id=1,
        session_id=1,
        revision_id=revision_id,
        revision_created_at=float(revision_id),
        current_understanding=understanding,
        actor="root",
        identity_bindings=bindings,
        now=float(revision_id) + 0.1,
    )


def test_shared_element_has_two_stable_many_to_many_relations():
    conn = _conn()
    graph = _confirm(conn, 1, _understanding())

    shared = next(node for node in graph["nodes"] if node["name"] == "Shared")
    assert len([node for node in graph["nodes"] if node["name"] == "Shared"]) == 1
    assert len(graph["relations"]) == 2
    assert {
        relation["supporting_entity_id"] for relation in graph["relations"]
    } == {shared["entity_id"]}


def test_explicit_rename_keeps_identity_and_does_not_change_semantic_scope():
    conn = _conn()
    first = _confirm(conn, 1, _understanding())
    old_a = next(node for node in first["nodes"] if node["name"] == "Core A")

    renamed_understanding = _understanding(a_name="Renamed Core A")
    second = _confirm(
        conn,
        2,
        renamed_understanding,
        bindings=[
            {
                "entity_kind": "core_capability",
                "current_name": "Renamed Core A",
                "entity_id": old_a["entity_id"],
            }
        ],
    )
    renamed_a = next(
        node for node in second["nodes"] if node["name"] == "Renamed Core A"
    )
    assert renamed_a["entity_id"] == old_a["entity_id"]
    changed_nodes, changed_relations = graph_change_sets(first, second)
    assert changed_nodes == set()
    assert changed_relations == set()

    # Same revision is fully idempotent: no second confirmation is appended.
    repeated = confirm_capability_graph(
        conn,
        system_id=1,
        session_id=1,
        revision_id=2,
        revision_created_at=2.0,
        current_understanding=renamed_understanding,
        actor="someone-else",
        identity_bindings=[
            {
                "entity_kind": "core_capability",
                "current_name": "Renamed Core A",
                "entity_id": old_a["entity_id"],
            }
        ],
        now=9.0,
    )
    assert repeated["confirmation_id"] == second["confirmation_id"]
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM understanding_capability_confirmation"
    ).fetchone()["n"]
    assert count == 2


def test_rename_without_binding_is_remove_and_add_safe_side():
    conn = _conn()
    first = _confirm(conn, 1, _understanding())
    old_a = next(node for node in first["nodes"] if node["name"] == "Core A")
    second = _confirm(conn, 2, _understanding(a_name="Renamed Core A"))
    new_a = next(
        node for node in second["nodes"] if node["name"] == "Renamed Core A"
    )
    assert new_a["entity_id"] != old_a["entity_id"]
    changed_nodes, changed_relations = graph_change_sets(first, second)
    assert old_a["entity_id"] in changed_nodes
    assert new_a["entity_id"] in changed_nodes
    assert changed_relations


def test_removing_one_shared_relation_does_not_change_the_other():
    conn = _conn()
    first = _confirm(conn, 1, _understanding())
    by_parent = {
        relation["supported_entity_id"]: relation
        for relation in first["relations"]
    }
    node_by_name = {node["name"]: node for node in first["nodes"]}
    a_relation = by_parent[node_by_name["Core A"]["entity_id"]]
    b_relation = by_parent[node_by_name["Core B"]["entity_id"]]

    second = _confirm(conn, 2, _understanding(a_children=[]))
    changed_nodes, changed_relations = graph_change_sets(first, second)
    assert changed_nodes == set()
    assert a_relation["relation_id"] in changed_relations
    assert b_relation["relation_id"] not in changed_relations


def test_parent_semantic_change_invalidates_only_its_shared_relation_context():
    conn = _conn()
    first = _confirm(conn, 1, _understanding())
    node_by_name = {node["name"]: node for node in first["nodes"]}
    relation_by_parent = {
        relation["supported_entity_id"]: relation
        for relation in first["relations"]
    }

    second = _confirm(conn, 2, _understanding(a_summary="A changed"))
    changed_nodes, changed_relations = graph_change_sets(first, second)

    assert changed_nodes == {node_by_name["Core A"]["entity_id"]}
    assert relation_by_parent[node_by_name["Core A"]["entity_id"]][
        "relation_id"
    ] in changed_relations
    assert relation_by_parent[node_by_name["Core B"]["entity_id"]][
        "relation_id"
    ] not in changed_relations


def test_ambiguous_identity_is_rejected_atomically():
    conn = _conn()
    first = _confirm(conn, 1, _understanding())
    a_id = next(node for node in first["nodes"] if node["name"] == "Core A")[
        "entity_id"
    ]
    bad = _understanding(a_name="A1")
    bad["core_capabilities"][1]["name"] = "A2"
    _revision(conn, 2, bad)

    with pytest.raises(CapabilityGraphError):
        confirm_capability_graph(
            conn,
            system_id=1,
            session_id=1,
            revision_id=2,
            revision_created_at=2.0,
            current_understanding=bad,
            actor="root",
            identity_bindings=[
                {
                    "entity_kind": "core_capability",
                    "current_name": "A1",
                    "entity_id": a_id,
                },
                {
                    "entity_kind": "core_capability",
                    "current_name": "A2",
                    "entity_id": a_id,
                },
            ],
            now=2.1,
        )
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM understanding_capability_confirmation
           WHERE source_revision_id = 2"""
    ).fetchone()["n"] == 0


def test_identity_binding_cannot_cross_system_boundary():
    conn = _conn()
    conn.execute(
        """INSERT INTO systems
            (id, name, environment, description, created_at, updated_at)
           VALUES (2, 'B', 'test', '', 1.0, 1.0)"""
    )
    foreign_entity_id = conn.execute(
        """INSERT INTO understanding_capability_entity
            (system_id, entity_kind, created_at)
           VALUES (2, 'core_capability', 1.0)"""
    ).lastrowid
    understanding = _understanding(a_name="Foreign A")
    _revision(conn, 1, understanding)

    with pytest.raises(CapabilityGraphError):
        confirm_capability_graph(
            conn,
            system_id=1,
            session_id=1,
            revision_id=1,
            revision_created_at=1.0,
            current_understanding=understanding,
            actor="root",
            identity_bindings=[
                {
                    "entity_kind": "core_capability",
                    "current_name": "Foreign A",
                    "entity_id": foreign_entity_id,
                }
            ],
            now=1.1,
        )

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM understanding_capability_confirmation"
    ).fetchone()["n"] == 0


def test_exact_identity_is_stable_across_sessions_in_the_same_system():
    conn = _conn()
    first = _confirm(conn, 1, _understanding())
    first_ids = {
        (node["entity_kind"], node["name"]): node["entity_id"]
        for node in first["nodes"]
    }
    conn.execute(
        """INSERT INTO interview_session
            (id, system_id, snapshot_id, title, focus, status, stage,
             created_at, updated_at)
           VALUES (2, 1, 1, '', '', 'open', 'purpose_confirmation', 2.0, 2.0)"""
    )
    conn.execute(
        """INSERT INTO understanding_revision
            (id, session_id, system_id, snapshot_id, intelligence_run_id,
             current_understanding, gap_analysis, created_at)
           VALUES (2, 2, 1, 1, NULL, ?, NULL, 2.0)""",
        (json.dumps(_understanding()),),
    )

    second = confirm_capability_graph(
        conn,
        system_id=1,
        session_id=2,
        revision_id=2,
        revision_created_at=2.0,
        current_understanding=_understanding(),
        actor="root",
        now=2.1,
    )
    second_ids = {
        (node["entity_kind"], node["name"]): node["entity_id"]
        for node in second["nodes"]
    }
    assert second_ids == first_ids
    assert second["base_confirmation_id"] == first["confirmation_id"]
