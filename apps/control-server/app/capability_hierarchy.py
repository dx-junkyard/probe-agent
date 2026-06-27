"""Source-backed capability hierarchy builder (Issue #56).

Aggregates #54 source-authored explanation metadata and #55 hash provenance,
together with deterministic structural facts (code symbols, code entrypoints,
the latest System Profile draft), into a hierarchy:

    System Purpose
      Core Capability
        Capability Element  -> source symbol / API entrypoint
        Supporting Element  -> DB / filesystem / external HTTP / queue / job / CLI

The deterministic builder never infers grouping from free text: it groups only
by the author-written ``capability`` field. Open-ended grouping of unclassified
API entrypoints is delegated to a reasoning model, which fails closed (no
heuristic fallback). Every node records its provenance kind so source-authored
explanation, deterministic structural fact, and reasoning interpretation stay
visibly separate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"

# Provenance kinds (see models.ProvenanceKind).
SOURCE_AUTHORED = "source_authored"
STRUCTURAL = "structural"
REASONING_LLM = "reasoning_llm"

# Map #54 state_effects / element kinds to supporting-element kinds.
_STATE_EFFECT_KINDS = {
    "database-read": "database",
    "database-write": "database",
    "network": "external-http",
    "external-api": "external-http",
    "filesystem": "filesystem",
    "cache": "cache",
    "queue": "queue",
}
_ENTRYPOINT_SUPPORTING_KIND = {
    "message_queue": "queue",
    "scheduled_job": "scheduled-job",
    "cli": "cli",
}


@dataclass
class SymbolRecord:
    symbol_id: int
    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    file_content_hash: Optional[str] = None
    symbol_source_hash: Optional[str] = None
    # #54 metadata (None when the symbol has no probe-agent block)
    has_metadata: bool = False
    role: Optional[str] = None
    capability: Optional[str] = None
    element_type: Optional[str] = None
    system_purpose: Optional[str] = None
    operation_kind: Optional[str] = None
    consumers: List[str] = field(default_factory=list)
    state_effects: List[str] = field(default_factory=list)
    probe_value: Optional[str] = None
    explanation_hash: Optional[str] = None


@dataclass
class EntrypointRecord:
    entrypoint_id: int
    category: str  # api | message_queue | scheduled_job | cli | function
    label: str
    operation: Optional[str] = None
    route_method: Optional[str] = None
    route_path: Optional[str] = None
    handler_symbol_id: Optional[int] = None
    handler_path: str = ""
    handler_qualified_name: str = ""
    line_start: int = 0
    line_end: int = 0


@dataclass
class HierarchyNode:
    node_type: str  # purpose | capability | element | supporting
    name: str
    summary: str = ""
    capability_key: Optional[str] = None
    element_role: Optional[str] = None
    operation_kind: Optional[str] = None
    probe_value: Optional[str] = None
    supporting_kind: Optional[str] = None
    classification: Optional[str] = None  # classified | unclassified
    symbol_id: Optional[int] = None
    entrypoint_id: Optional[int] = None
    feature_id: Optional[str] = None
    system_profile_draft_id: Optional[int] = None
    path: Optional[str] = None
    qualified_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    file_content_hash: Optional[str] = None
    symbol_source_hash: Optional[str] = None
    explanation_hash: Optional[str] = None
    provenance_kind: str = STRUCTURAL
    decision_method: str = "deterministic"
    provider: Optional[str] = None
    model: Optional[str] = None
    children: List["HierarchyNode"] = field(default_factory=list)


@dataclass
class BuiltHierarchy:
    purpose: Optional[HierarchyNode]
    capabilities: List[HierarchyNode]
    unclassified_elements: List[HierarchyNode]
    unattached_supporting: List[HierarchyNode]

    def capability_by_key(self, key: str) -> Optional[HierarchyNode]:
        for cap in self.capabilities:
            if cap.capability_key == key:
                return cap
        return None


def _apply_anchor(node: HierarchyNode, sym: SymbolRecord) -> None:
    node.symbol_id = sym.symbol_id
    node.path = sym.path
    node.qualified_name = sym.qualified_name
    node.start_line = sym.start_line
    node.end_line = sym.end_line
    node.file_content_hash = sym.file_content_hash
    node.symbol_source_hash = sym.symbol_source_hash
    node.explanation_hash = sym.explanation_hash


def _apply_handler_provenance(node: HierarchyNode, handler: Optional[SymbolRecord]) -> None:
    """Copy hash provenance from a resolved entrypoint handler symbol.

    Entrypoint nodes already carry the handler path/line range; this adds the
    symbol id and #55 hashes so every entrypoint-derived claim (API or backend
    boundary) can participate in later drift detection.
    """
    if handler is None:
        return
    node.symbol_id = handler.symbol_id
    node.file_content_hash = handler.file_content_hash
    node.symbol_source_hash = handler.symbol_source_hash
    node.explanation_hash = handler.explanation_hash


def build_hierarchy(
    symbols: List[SymbolRecord],
    entrypoints: List[EntrypointRecord],
    system_profile_draft: Optional[Dict] = None,
    feature_links: Optional[Dict[int, str]] = None,
) -> BuiltHierarchy:
    """Deterministically build the hierarchy from source-authored metadata.

    Grouping uses only the author-written ``capability`` field; nothing is
    inferred from free text. Symbols/entrypoints without a capability become
    ``unclassified`` rather than being guessed at.

    ``feature_links`` maps ``symbol_id -> feature_id`` for accepted
    Feature-to-Code links (#24), connecting hierarchy nodes back to the existing
    Feature Map. It is a deterministic structural link, not a new claim.
    """
    feature_links = feature_links or {}

    def _feature_for(symbol_id: Optional[int]) -> Optional[str]:
        return feature_links.get(symbol_id) if symbol_id is not None else None

    symbols_by_id = {s.symbol_id: s for s in symbols}
    capabilities: Dict[str, HierarchyNode] = {}
    # Track which (capability_key, supporting_kind, symbol_id) we already added.
    seen_supporting = set()

    def _ensure_capability(key: str) -> HierarchyNode:
        node = capabilities.get(key)
        if node is None:
            node = HierarchyNode(
                node_type="capability",
                name=key,
                capability_key=key,
                provenance_kind=SOURCE_AUTHORED,
                decision_method="deterministic",
            )
            capabilities[key] = node
        return node

    # 1. Capabilities and capability elements from source-authored metadata.
    for sym in symbols:
        if not sym.has_metadata or not sym.capability:
            continue
        cap = _ensure_capability(sym.capability)
        etype = sym.element_type or "element"
        if etype in ("core", "capability"):
            # This symbol defines/anchors the capability: use it for summary.
            if not cap.summary and sym.role:
                cap.summary = sym.role
            if cap.path is None:
                _apply_anchor(cap, sym)

        feature_id = _feature_for(sym.symbol_id)
        if etype in ("supporting", "boundary"):
            supporting = HierarchyNode(
                node_type="supporting",
                name=sym.role or sym.qualified_name,
                summary=sym.role or "",
                capability_key=sym.capability,
                supporting_kind="boundary",
                operation_kind=sym.operation_kind,
                feature_id=feature_id,
                provenance_kind=SOURCE_AUTHORED,
                decision_method="deterministic",
            )
            _apply_anchor(supporting, sym)
            cap.children.append(supporting)
        else:
            element = HierarchyNode(
                node_type="element",
                name=sym.qualified_name.split(".")[-1] or sym.qualified_name,
                summary=sym.role or "",
                capability_key=sym.capability,
                element_role=sym.role,
                operation_kind=sym.operation_kind,
                probe_value=sym.probe_value,
                classification="classified",
                feature_id=feature_id,
                provenance_kind=SOURCE_AUTHORED,
                decision_method="deterministic",
            )
            _apply_anchor(element, sym)
            cap.children.append(element)

        # State effects declared by the symbol become supporting elements.
        for effect in sym.state_effects:
            kind = _STATE_EFFECT_KINDS.get(effect)
            if kind is None or effect == "none":
                continue
            dedup = (sym.capability, kind, sym.symbol_id)
            if dedup in seen_supporting:
                continue
            seen_supporting.add(dedup)
            support = HierarchyNode(
                node_type="supporting",
                name=kind,
                summary=f"{effect} declared by {sym.qualified_name}",
                capability_key=sym.capability,
                supporting_kind=kind,
                feature_id=feature_id,
                provenance_kind=SOURCE_AUTHORED,
                decision_method="deterministic",
            )
            _apply_anchor(support, sym)
            cap.children.append(support)

    # 2. System purpose: prefer source-authored, else link the latest draft.
    purpose: Optional[HierarchyNode] = None
    for sym in symbols:
        if sym.has_metadata and sym.system_purpose:
            purpose = HierarchyNode(
                node_type="purpose",
                name="System Purpose",
                summary=sym.system_purpose,
                provenance_kind=SOURCE_AUTHORED,
                decision_method="deterministic",
            )
            _apply_anchor(purpose, sym)
            break
    if purpose is None and system_profile_draft is not None:
        purpose = HierarchyNode(
            node_type="purpose",
            name=system_profile_draft.get("name") or "System Purpose",
            summary=system_profile_draft.get("purpose") or "",
            system_profile_draft_id=system_profile_draft.get("id"),
            provenance_kind=STRUCTURAL,
            decision_method="deterministic",
        )

    # 3. API entrypoints become elements; classify by the handler's capability.
    unclassified: List[HierarchyNode] = []
    unattached_supporting: List[HierarchyNode] = []
    for ep in entrypoints:
        if ep.category == "api":
            handler = (
                symbols_by_id.get(ep.handler_symbol_id)
                if ep.handler_symbol_id is not None
                else None
            )
            cap_key = handler.capability if (handler and handler.has_metadata) else None
            node = HierarchyNode(
                node_type="element",
                name=ep.label,
                summary=ep.route_path or ep.operation or "",
                element_role=ep.operation,
                entrypoint_id=ep.entrypoint_id,
                path=ep.handler_path or None,
                qualified_name=ep.handler_qualified_name or None,
                start_line=ep.line_start or None,
                end_line=ep.line_end or None,
                provenance_kind=STRUCTURAL,
                decision_method="deterministic",
            )
            _apply_handler_provenance(node, handler)
            node.feature_id = _feature_for(handler.symbol_id) if handler else None
            if cap_key and cap_key in capabilities:
                node.classification = "classified"
                node.capability_key = cap_key
                node.provenance_kind = SOURCE_AUTHORED
                capabilities[cap_key].children.append(node)
            else:
                node.classification = "unclassified"
                unclassified.append(node)
        elif ep.category in _ENTRYPOINT_SUPPORTING_KIND:
            # Message queues / scheduled jobs / CLIs are supporting boundaries.
            kind = _ENTRYPOINT_SUPPORTING_KIND[ep.category]
            handler = (
                symbols_by_id.get(ep.handler_symbol_id)
                if ep.handler_symbol_id is not None
                else None
            )
            cap_key = handler.capability if (handler and handler.has_metadata) else None
            node = HierarchyNode(
                node_type="supporting",
                name=ep.label,
                summary=ep.operation or "",
                supporting_kind=kind,
                entrypoint_id=ep.entrypoint_id,
                capability_key=cap_key,
                path=ep.handler_path or None,
                qualified_name=ep.handler_qualified_name or None,
                start_line=ep.line_start or None,
                end_line=ep.line_end or None,
                provenance_kind=STRUCTURAL,
                decision_method="deterministic",
            )
            _apply_handler_provenance(node, handler)
            node.feature_id = _feature_for(handler.symbol_id) if handler else None
            if cap_key and cap_key in capabilities:
                capabilities[cap_key].children.append(node)
            else:
                unattached_supporting.append(node)

    ordered_caps = [capabilities[k] for k in sorted(capabilities.keys())]
    return BuiltHierarchy(
        purpose=purpose,
        capabilities=ordered_caps,
        unclassified_elements=unclassified,
        unattached_supporting=unattached_supporting,
    )


# ---------------------------------------------------------------------------
# Reasoning-assisted grouping of unclassified API entrypoints (fail closed)
# ---------------------------------------------------------------------------


@dataclass
class GroupingAssignment:
    entrypoint_id: int
    capability_key: str
    reason: str


@dataclass
class GroupingResult:
    provider: str
    model: str
    is_mock: bool
    assignments: List[GroupingAssignment] = field(default_factory=list)
    error: Optional[str] = None


class GroupingValidationError(ValueError):
    pass


_SYSTEM_PROMPT = """\
You group unclassified backend API entrypoints under existing source-authored
capabilities. Only assign an entrypoint when the evidence clearly supports it.
Respond with ONLY valid JSON; never invent capability keys."""


def _build_grouping_prompt(
    capabilities: List[HierarchyNode], unclassified: List[HierarchyNode]
) -> str:
    cap_lines = [
        f"- key: {c.capability_key} | summary: {c.summary or '(none)'}"
        for c in capabilities
    ]
    ep_lines = [
        f"- entrypoint_id: {e.entrypoint_id} | label: {e.name} | "
        f"handler: {e.qualified_name or '(unknown)'}"
        for e in unclassified
    ]
    return (
        "Existing capabilities:\n"
        + "\n".join(cap_lines)
        + "\n\nUnclassified API entrypoints:\n"
        + "\n".join(ep_lines)
        + "\n\nReturn JSON: {\"assignments\": [{\"entrypoint_id\": int, "
        "\"capability_key\": string, \"reason\": string}]}. Omit entrypoints "
        "you cannot confidently assign."
    )


def parse_grouping_response(
    raw_json: str,
    valid_capability_keys: set,
    valid_entrypoint_ids: set,
) -> List[GroupingAssignment]:
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise GroupingValidationError("response must be a JSON object")
    raw_assignments = data.get("assignments")
    if not isinstance(raw_assignments, list):
        raise GroupingValidationError("assignments must be an array")
    out: List[GroupingAssignment] = []
    for item in raw_assignments:
        if not isinstance(item, dict):
            raise GroupingValidationError("assignment items must be objects")
        try:
            entrypoint_id = int(item.get("entrypoint_id"))
        except (TypeError, ValueError) as exc:
            raise GroupingValidationError("entrypoint_id must be an integer") from exc
        capability_key = str(item.get("capability_key", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if capability_key not in valid_capability_keys:
            raise GroupingValidationError(
                f"unknown capability_key: {capability_key!r}"
            )
        if entrypoint_id not in valid_entrypoint_ids:
            raise GroupingValidationError(
                f"entrypoint_id not in unclassified set: {entrypoint_id}"
            )
        if not reason:
            raise GroupingValidationError("assignment reason is required")
        out.append(
            GroupingAssignment(
                entrypoint_id=entrypoint_id,
                capability_key=capability_key,
                reason=reason,
            )
        )
    return out


def propose_capability_grouping(
    client: LLMClient,
    config: LLMConfig,
    capabilities: List[HierarchyNode],
    unclassified: List[HierarchyNode],
) -> GroupingResult:
    """Ask a reasoning model to assign unclassified API entrypoints.

    Mock providers return no assignments (visibly marked). Any provider/parse/
    validation failure is returned as an error so the caller can fail closed.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock:
        return GroupingResult(provider="mock", model="mock", is_mock=True, assignments=[])

    valid_caps = {c.capability_key for c in capabilities if c.capability_key}
    valid_eps = {e.entrypoint_id for e in unclassified if e.entrypoint_id is not None}
    if not valid_caps or not valid_eps:
        return GroupingResult(
            provider=config.provider, model=config.model, is_mock=False, assignments=[]
        )

    prompt = _build_grouping_prompt(capabilities, unclassified)
    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
    except LLMError as exc:
        return GroupingResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=str(exc),
        )

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        assignments = parse_grouping_response(cleaned, valid_caps, valid_eps)
    except (json.JSONDecodeError, GroupingValidationError, TypeError) as exc:
        return GroupingResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=f"Failed to parse grouping response: {exc}",
        )

    return GroupingResult(
        provider=config.provider, model=config.model, is_mock=False,
        assignments=assignments,
    )
