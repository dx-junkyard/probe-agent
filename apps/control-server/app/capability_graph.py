"""Canonical, manually-confirmed capability composition (Issue #312).

``current_understanding`` remains a reasoning-model proposal.  This module
materializes only a human-confirmed revision into stable System-scoped entity
and relation identities.  Names never determine identity across a rename:
an explicit binding is required.  Relations are many-to-many, so a shared
lower-level function can support more than one Core Capability.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


ENTITY_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("core_capabilities", "core_capability"),
    ("capability_elements", "capability_element"),
    ("supporting_elements", "supporting_element"),
    ("api_boundaries", "api_boundary"),
)
ENTITY_KINDS = tuple(kind for _, kind in ENTITY_SECTIONS)
SECTION_BY_KIND = {kind: section for section, kind in ENTITY_SECTIONS}
ALLOWED_RELATION_PAIRS = {
    ("core_capability", "capability_element"),
    ("core_capability", "supporting_element"),
    ("core_capability", "api_boundary"),
    ("capability_element", "supporting_element"),
    ("capability_element", "api_boundary"),
    ("supporting_element", "api_boundary"),
}


class CapabilityGraphError(ValueError):
    """The proposed confirmation cannot be resolved without guessing."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sorted_strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if isinstance(item, str) and item})


def _normalized_evidence(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "path": item.get("path") or "",
                "start_line": item.get("start_line") or 0,
                "end_line": item.get("end_line") or 0,
                "summary": item.get("summary") or "",
            }
        )
    result.sort(
        key=lambda item: (
            item["path"],
            item["start_line"],
            item["end_line"],
            item["summary"],
        )
    )
    return result


def _node_payload(kind: str, item: Mapping[str, Any]) -> Dict[str, Any]:
    """Meaning-bearing payload; display name/confidence/children are separate.

    Excluding the display name makes an explicitly bound pure rename a
    non-semantic change.  ``children`` belongs to relation composition, not
    node meaning.  Model confidence is deliberately not an authoritative
    capability definition.
    """

    return {
        "kind": kind,
        "summary": item.get("summary") or "",
        "why_core": item.get("why_core") or "",
        "evidence": _normalized_evidence(item.get("evidence")),
        "related_docs": _sorted_strings(item.get("related_docs")),
        "related_apis": _sorted_strings(item.get("related_apis")),
    }


def _proposed_nodes(
    current_understanding: Mapping[str, Any],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    nodes: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for section, kind in ENTITY_SECTIONS:
        items = current_understanding.get(section) or []
        if not isinstance(items, list):
            raise CapabilityGraphError(f"{section} must be a list")
        for item in items:
            if not isinstance(item, dict):
                raise CapabilityGraphError(f"{section} contains a non-object item")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise CapabilityGraphError(f"{section} contains an item without a name")
            key = (kind, name.strip())
            if key in nodes:
                raise CapabilityGraphError(
                    f"duplicate capability name in {section}: {name.strip()!r}"
                )
            nodes[key] = dict(item)
    return nodes


def _latest_confirmation_id(
    conn: sqlite3.Connection,
    system_id: int,
    session_id: Optional[int] = None,
) -> Optional[int]:
    params: Tuple[int, ...]
    where = "system_id = ?"
    params = (system_id,)
    if session_id is not None:
        where += " AND session_id = ?"
        params = (system_id, session_id)
    row = conn.execute(
        f"""SELECT id FROM understanding_capability_confirmation
            WHERE {where}
            ORDER BY id DESC LIMIT 1""",
        params,
    ).fetchone()
    return row["id"] if row else None


def _previous_entities_by_name(
    conn: sqlite3.Connection, confirmation_id: Optional[int]
) -> Dict[Tuple[str, str], int]:
    if confirmation_id is None:
        return {}
    rows = conn.execute(
        """SELECT entity_kind, name, entity_id
           FROM understanding_capability_entity_version
           WHERE confirmation_id = ?""",
        (confirmation_id,),
    ).fetchall()
    return {(row["entity_kind"], row["name"]): row["entity_id"] for row in rows}


def _validate_identity_bindings(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    nodes: Mapping[Tuple[str, str], Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
) -> Dict[Tuple[str, str], int]:
    result: Dict[Tuple[str, str], int] = {}
    used_entity_ids: Set[int] = set()
    for binding in bindings:
        kind = binding.get("entity_kind")
        name = binding.get("current_name")
        entity_id = binding.get("entity_id")
        key = (kind, name)
        if kind not in ENTITY_KINDS or key not in nodes:
            raise CapabilityGraphError(
                f"identity binding target does not exist in the current revision: {key!r}"
            )
        if key in result:
            raise CapabilityGraphError(f"duplicate identity binding: {key!r}")
        row = conn.execute(
            """SELECT id, entity_kind FROM understanding_capability_entity
               WHERE id = ? AND system_id = ?""",
            (entity_id, system_id),
        ).fetchone()
        if row is None or row["entity_kind"] != kind:
            raise CapabilityGraphError(
                f"entity {entity_id!r} is not a {kind!r} in this System"
            )
        if entity_id in used_entity_ids:
            raise CapabilityGraphError(
                f"entity {entity_id!r} cannot identify multiple current nodes"
            )
        result[key] = entity_id
        used_entity_ids.add(entity_id)
    return result


def _relation_inputs(
    nodes: Mapping[Tuple[str, str], Mapping[str, Any]],
    explicit_relations: Optional[Sequence[Mapping[str, Any]]],
) -> List[Dict[str, str]]:
    if explicit_relations is not None:
        result: List[Dict[str, str]] = []
        seen: Set[Tuple[str, str, str, str]] = set()
        for relation in explicit_relations:
            parent_kind = relation.get("supported_kind")
            parent_name = relation.get("supported_name")
            child_kind = relation.get("supporting_kind")
            child_name = relation.get("supporting_name")
            if (parent_kind, child_kind) not in ALLOWED_RELATION_PAIRS:
                raise CapabilityGraphError(
                    f"unsupported capability relation: {parent_kind!r} -> {child_kind!r}"
                )
            if (parent_kind, parent_name) not in nodes:
                raise CapabilityGraphError(
                    f"relation parent is not in the current revision: {parent_name!r}"
                )
            if (child_kind, child_name) not in nodes:
                raise CapabilityGraphError(
                    f"relation child is not in the current revision: {child_name!r}"
                )
            identity = (parent_kind, parent_name, child_kind, child_name)
            if identity in seen:
                raise CapabilityGraphError(f"duplicate capability relation: {identity!r}")
            seen.add(identity)
            result.append(
                {
                    "supported_kind": parent_kind,
                    "supported_name": parent_name,
                    "supporting_kind": child_kind,
                    "supporting_name": child_name,
                    "role": relation.get("role") or "",
                    "scope": relation.get("scope") or "",
                }
            )
        return sorted(
            result,
            key=lambda item: (
                item["supported_kind"],
                item["supported_name"],
                item["supporting_kind"],
                item["supporting_name"],
            ),
        )

    result = []
    seen: Set[Tuple[str, str, str, str]] = set()
    for (parent_kind, parent_name), item in sorted(nodes.items()):
        children = item.get("children") or []
        if not isinstance(children, list):
            raise CapabilityGraphError(f"children for {parent_name!r} must be a list")
        allowed_child_kinds = {
            child_kind
            for supported_kind, child_kind in ALLOWED_RELATION_PAIRS
            if supported_kind == parent_kind
        }
        if not allowed_child_kinds and children:
            raise CapabilityGraphError(
                f"{parent_kind} {parent_name!r} cannot have capability children"
            )
        for child_name in children:
            if not isinstance(child_name, str) or not child_name.strip():
                raise CapabilityGraphError(
                    f"{parent_name!r} contains an invalid child name"
                )
            matches = [
                child_kind
                for child_kind in sorted(allowed_child_kinds)
                if (child_kind, child_name.strip()) in nodes
            ]
            if len(matches) != 1:
                raise CapabilityGraphError(
                    f"child {child_name.strip()!r} of {parent_name!r} "
                    "must resolve to exactly one lower-level node"
                )
            child_kind = matches[0]
            identity = (parent_kind, parent_name, child_kind, child_name.strip())
            if identity in seen:
                raise CapabilityGraphError(f"duplicate capability relation: {identity!r}")
            seen.add(identity)
            result.append(
                {
                    "supported_kind": parent_kind,
                    "supported_name": parent_name,
                    "supporting_kind": child_kind,
                    "supporting_name": child_name.strip(),
                    "role": "",
                    "scope": "",
                }
            )
    return result


def confirm_capability_graph(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    revision_id: int,
    revision_created_at: float,
    current_understanding: Mapping[str, Any],
    actor: str,
    actor_user_id: Optional[int] = None,
    identity_bindings: Sequence[Mapping[str, Any]] = (),
    relations: Optional[Sequence[Mapping[str, Any]]] = None,
    now: float,
) -> Dict[str, Any]:
    """Append one atomic, human-confirmed canonical composition.

    Reconfirming the same revision is idempotent.  Exact kind+name matches
    reuse the preceding identity; rename reuse happens only through an
    explicit binding supplied by the human confirmation request.
    """

    nodes = _proposed_nodes(current_understanding)
    relation_inputs = _relation_inputs(nodes, relations)
    normalized_bindings = sorted(
        [
            {
                "entity_kind": binding.get("entity_kind"),
                "current_name": binding.get("current_name"),
                "entity_id": binding.get("entity_id"),
            }
            for binding in identity_bindings
        ],
        key=lambda item: (
            str(item["entity_kind"]),
            str(item["current_name"]),
            str(item["entity_id"]),
        ),
    )
    request_digest = _digest(
        {
            "nodes": [
                {
                    "entity_kind": kind,
                    "name": name,
                    "payload": _node_payload(kind, item),
                }
                for (kind, name), item in sorted(nodes.items())
            ],
            "relations": relation_inputs,
            "identity_bindings": normalized_bindings,
        }
    )

    existing = conn.execute(
        """SELECT id, request_digest
           FROM understanding_capability_confirmation
           WHERE system_id = ? AND session_id = ? AND source_revision_id = ?""",
        (system_id, session_id, revision_id),
    ).fetchone()
    if existing is not None:
        if existing["request_digest"] != request_digest:
            raise CapabilityGraphError(
                "this Understanding revision was already confirmed with a "
                "different identity/relation request; create a new revision"
            )
        return load_confirmed_graph(conn, existing["id"], system_id=system_id)

    conn.execute("SAVEPOINT confirm_capability_graph")
    try:
        # Canonical entity identity is System-scoped, not Interview-session
        # scoped.  A new session therefore continues the latest confirmed
        # System identity for exact kind+name matches.
        previous_id = _latest_confirmation_id(conn, system_id)
        previous_by_name = _previous_entities_by_name(conn, previous_id)
        explicit = _validate_identity_bindings(
            conn,
            system_id=system_id,
            nodes=nodes,
            bindings=identity_bindings,
        )

        entity_ids: Dict[Tuple[str, str], int] = {}
        used_ids: Set[int] = set()
        node_versions: List[Dict[str, Any]] = []
        for (kind, name), item in sorted(nodes.items()):
            entity_id = explicit.get((kind, name))
            if entity_id is None:
                candidate = previous_by_name.get((kind, name))
                if candidate is not None and candidate not in used_ids:
                    entity_id = candidate
            if entity_id is None:
                cur = conn.execute(
                    """INSERT INTO understanding_capability_entity
                        (system_id, entity_kind, created_at)
                       VALUES (?, ?, ?)""",
                    (system_id, kind, now),
                )
                entity_id = cur.lastrowid
            if entity_id in used_ids:
                raise CapabilityGraphError(
                    f"entity {entity_id!r} cannot identify multiple current nodes"
                )
            used_ids.add(entity_id)
            entity_ids[(kind, name)] = entity_id
            payload = _node_payload(kind, item)
            node_versions.append(
                {
                    "entity_id": entity_id,
                    "entity_kind": kind,
                    "name": name,
                    "summary": item.get("summary") or "",
                    "semantic_digest": _digest(payload),
                    "payload_json": _canonical_json(payload),
                }
            )

        relation_versions: List[Dict[str, Any]] = []
        for relation in relation_inputs:
            parent_id = entity_ids[
                (relation["supported_kind"], relation["supported_name"])
            ]
            child_id = entity_ids[
                (relation["supporting_kind"], relation["supporting_name"])
            ]
            row = conn.execute(
                """SELECT id FROM understanding_capability_relation
                   WHERE system_id = ? AND supported_entity_id = ?
                     AND supporting_entity_id = ? AND relation_kind = 'supports'""",
                (system_id, parent_id, child_id),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    """INSERT INTO understanding_capability_relation
                        (system_id, supported_entity_id, supporting_entity_id,
                         relation_kind, created_at)
                       VALUES (?, ?, ?, 'supports', ?)""",
                    (system_id, parent_id, child_id, now),
                )
                relation_id = cur.lastrowid
            else:
                relation_id = row["id"]
            relation_payload = {
                "relation_id": relation_id,
                "supported_entity_id": parent_id,
                "supported_semantic_digest": next(
                    item["semantic_digest"]
                    for item in node_versions
                    if item["entity_id"] == parent_id
                ),
                "supporting_entity_id": child_id,
                "supporting_semantic_digest": next(
                    item["semantic_digest"]
                    for item in node_versions
                    if item["entity_id"] == child_id
                ),
                "role": relation["role"],
                "scope": relation["scope"],
            }
            relation_versions.append(
                {
                    "relation_id": relation_id,
                    "role": relation["role"],
                    "scope": relation["scope"],
                    "semantic_digest": _digest(relation_payload),
                }
            )

        composition_digest = _digest(
            {
                "nodes": [
                    {
                        "entity_id": item["entity_id"],
                        "entity_kind": item["entity_kind"],
                        "name": item["name"],
                        "semantic_digest": item["semantic_digest"],
                    }
                    for item in sorted(node_versions, key=lambda value: value["entity_id"])
                ],
                "relations": [
                    {
                        "relation_id": item["relation_id"],
                        "semantic_digest": item["semantic_digest"],
                    }
                    for item in sorted(
                        relation_versions, key=lambda value: value["relation_id"]
                    )
                ],
            }
        )
        cur = conn.execute(
            """INSERT INTO understanding_capability_confirmation
                (system_id, session_id, base_confirmation_id,
                 source_revision_id, source_revision_at,
                 composition_digest, request_digest, decided_by,
                 decided_by_user_id, decision_method, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?)""",
            (
                system_id,
                session_id,
                previous_id,
                revision_id,
                revision_created_at,
                composition_digest,
                request_digest,
                actor,
                actor_user_id,
                now,
            ),
        )
        confirmation_id = cur.lastrowid

        for item in node_versions:
            conn.execute(
                """INSERT INTO understanding_capability_entity_version
                    (system_id, confirmation_id, entity_id, entity_kind, name,
                     summary, semantic_digest, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id,
                    confirmation_id,
                    item["entity_id"],
                    item["entity_kind"],
                    item["name"],
                    item["summary"],
                    item["semantic_digest"],
                    item["payload_json"],
                    now,
                ),
            )
        for item in relation_versions:
            conn.execute(
                """INSERT INTO understanding_capability_relation_version
                    (system_id, confirmation_id, relation_id, role, scope,
                     semantic_digest, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id,
                    confirmation_id,
                    item["relation_id"],
                    item["role"],
                    item["scope"],
                    item["semantic_digest"],
                    now,
                ),
            )
        conn.execute("RELEASE SAVEPOINT confirm_capability_graph")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT confirm_capability_graph")
        conn.execute("RELEASE SAVEPOINT confirm_capability_graph")
        raise

    return load_confirmed_graph(conn, confirmation_id, system_id=system_id)


def load_confirmed_graph(
    conn: sqlite3.Connection,
    confirmation_id: int,
    *,
    system_id: int,
) -> Dict[str, Any]:
    confirmation = conn.execute(
        """SELECT * FROM understanding_capability_confirmation
           WHERE id = ? AND system_id = ?""",
        (confirmation_id, system_id),
    ).fetchone()
    if confirmation is None:
        raise CapabilityGraphError("capability confirmation not found in this System")
    node_rows = conn.execute(
        """SELECT entity_id, entity_kind, name, summary, semantic_digest,
                  payload_json
           FROM understanding_capability_entity_version
           WHERE confirmation_id = ? AND system_id = ?
           ORDER BY entity_id""",
        (confirmation_id, system_id),
    ).fetchall()
    relation_rows = conn.execute(
        """SELECT rv.relation_id, r.supported_entity_id,
                  r.supporting_entity_id, r.relation_kind,
                  rv.role, rv.scope, rv.semantic_digest
           FROM understanding_capability_relation_version rv
           JOIN understanding_capability_relation r ON r.id = rv.relation_id
           WHERE rv.confirmation_id = ? AND rv.system_id = ?
           ORDER BY rv.relation_id""",
        (confirmation_id, system_id),
    ).fetchall()
    return {
        "confirmation_id": confirmation["id"],
        "system_id": confirmation["system_id"],
        "session_id": confirmation["session_id"],
        "base_confirmation_id": confirmation["base_confirmation_id"],
        "source_revision_id": confirmation["source_revision_id"],
        "source_revision_at": confirmation["source_revision_at"],
        "composition_digest": confirmation["composition_digest"],
        "decided_by": confirmation["decided_by"],
        "decided_by_user_id": confirmation["decided_by_user_id"],
        "decision_method": confirmation["decision_method"],
        "created_at": confirmation["created_at"],
        "nodes": [
            {
                "entity_id": row["entity_id"],
                "entity_kind": row["entity_kind"],
                "name": row["name"],
                "summary": row["summary"],
                "semantic_digest": row["semantic_digest"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in node_rows
        ],
        "relations": [
            {
                "relation_id": row["relation_id"],
                "supported_entity_id": row["supported_entity_id"],
                "supporting_entity_id": row["supporting_entity_id"],
                "relation_kind": row["relation_kind"],
                "role": row["role"],
                "scope": row["scope"],
                "semantic_digest": row["semantic_digest"],
            }
            for row in relation_rows
        ],
    }


def confirmed_graph_for_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    revision_id: int,
) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT id FROM understanding_capability_confirmation
           WHERE system_id = ? AND session_id = ? AND source_revision_id = ?""",
        (system_id, session_id, revision_id),
    ).fetchone()
    if row is None:
        return None
    return load_confirmed_graph(conn, row["id"], system_id=system_id)


def latest_confirmed_graph(
    conn: sqlite3.Connection, *, system_id: int, session_id: int
) -> Optional[Dict[str, Any]]:
    confirmation_id = _latest_confirmation_id(conn, system_id, session_id)
    if confirmation_id is None:
        return None
    return load_confirmed_graph(conn, confirmation_id, system_id=system_id)


def latest_system_confirmed_graph(
    conn: sqlite3.Connection, *, system_id: int
) -> Optional[Dict[str, Any]]:
    """Return the System-wide canonical head, regardless of source session."""

    confirmation_id = _latest_confirmation_id(conn, system_id)
    if confirmation_id is None:
        return None
    return load_confirmed_graph(conn, confirmation_id, system_id=system_id)


def validate_dependencies(
    graph: Optional[Mapping[str, Any]],
    *,
    entity_ids: Iterable[int],
    relation_ids: Iterable[int],
) -> List[Dict[str, Any]]:
    """Resolve exact ids to finite captured digests; never fuzzy-match."""

    requested_entities = sorted(set(entity_ids))
    requested_relations = sorted(set(relation_ids))
    if graph is None:
        if requested_entities or requested_relations:
            raise CapabilityGraphError(
                "alignment dependencies require a confirmed capability graph"
            )
        return []
    nodes = {item["entity_id"]: item for item in graph["nodes"]}
    relations = {item["relation_id"]: item for item in graph["relations"]}
    unknown_entities = [value for value in requested_entities if value not in nodes]
    unknown_relations = [value for value in requested_relations if value not in relations]
    if unknown_entities or unknown_relations:
        raise CapabilityGraphError(
            "alignment dependencies contain ids outside the confirmed graph: "
            f"entities={unknown_entities}, relations={unknown_relations}"
        )
    return [
        {
            "target_kind": "entity",
            "entity_id": entity_id,
            "relation_id": None,
            "captured_digest": nodes[entity_id]["semantic_digest"],
        }
        for entity_id in requested_entities
    ] + [
        {
            "target_kind": "relation",
            "entity_id": None,
            "relation_id": relation_id,
            "captured_digest": relations[relation_id]["semantic_digest"],
        }
        for relation_id in requested_relations
    ]


def graph_change_sets(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> Tuple[Set[int], Set[int]]:
    """Return semantic entity changes and relation composition changes.

    Display-name-only rename is not a semantic entity change.  Relation
    add/remove is represented by presence in only one confirmed graph.
    """

    previous_nodes = {
        item["entity_id"]: item["semantic_digest"] for item in previous["nodes"]
    }
    current_nodes = {
        item["entity_id"]: item["semantic_digest"] for item in current["nodes"]
    }
    node_ids = set(previous_nodes) | set(current_nodes)
    changed_nodes = {
        entity_id
        for entity_id in node_ids
        if previous_nodes.get(entity_id) != current_nodes.get(entity_id)
    }

    previous_relations = {
        item["relation_id"]: item["semantic_digest"]
        for item in previous["relations"]
    }
    current_relations = {
        item["relation_id"]: item["semantic_digest"] for item in current["relations"]
    }
    relation_ids = set(previous_relations) | set(current_relations)
    changed_relations = {
        relation_id
        for relation_id in relation_ids
        if previous_relations.get(relation_id) != current_relations.get(relation_id)
    }
    return changed_nodes, changed_relations
