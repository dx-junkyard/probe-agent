"""LLM-assisted analyzer proposal (Issue #149, Phase 4b).

Turns a natural-language intent into a validated ``trace_analyzer_spec``. This
is an open-ended judgement, so a reasoning-model LLM is required (Principle 6);
there is no heuristic fallback. The mock provider is an explicit smoke path:
it produces a deterministic spec built from the real context and is always
marked ``is_mock`` so it is never mistaken for a model proposal.

Failures (missing config, API error, invalid structured output, references to
identifiers that do not exist) fail closed — no spec is saved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from .trace_analyzer import AnalyzerSpecError, compile_spec

PROMPT_VERSION = "analyzer-propose-v1"
SCHEMA_VERSION = "trace-analyzer-spec-v1"

_SYSTEM_PROMPT = (
    "You convert a developer's natural-language intent into a declarative, "
    "read-only trace analyzer spec. Respond with a SINGLE JSON object matching "
    "the trace_analyzer_spec contract: keys 'source' (always 'trace_projections'), "
    "optional 'filter' (entity {type,id}, components[], projection_name, phases[], "
    "time_window{start,end}), required 'select' (list of {name, path}), optional "
    "'group_by' (names from select), 'order_by' (list of {name, dir}), and "
    "'compare' ({phases:[a,b], fields:[...]}). Paths use the restricted grammar "
    "$.a.b / $.items[*].x / $.field[0] and address a row shaped as "
    "{trace_id, component_id, projection_name, phase, timestamp, fields, metrics, "
    "samples, entities}. Only reference components, entity types, and projection "
    "field names that appear in the provided context. Output JSON only."
)


@dataclass
class ProposalContext:
    components: List[str] = field(default_factory=list)
    entity_types: List[str] = field(default_factory=list)
    projection_names: List[str] = field(default_factory=list)
    field_names: List[str] = field(default_factory=list)
    phases: List[str] = field(default_factory=list)


@dataclass
class ProposalResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    spec: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def build_proposal_context(conn, system_id: int) -> ProposalContext:
    """Deterministically gather the identifiers a proposal may reference."""
    components = [
        r["component_id"]
        for r in conn.execute(
            "SELECT component_id FROM components WHERE system_id = ? ORDER BY component_id",
            (system_id,),
        ).fetchall()
    ]
    entity_types = [
        r["entity_type"]
        for r in conn.execute(
            "SELECT DISTINCT entity_type FROM trace_entities WHERE system_id = ? "
            "ORDER BY entity_type",
            (system_id,),
        ).fetchall()
    ]
    proj_rows = conn.execute(
        "SELECT DISTINCT projection_name, data_json FROM trace_projections "
        "WHERE system_id = ?",
        (system_id,),
    ).fetchall()
    projection_names = sorted({r["projection_name"] for r in proj_rows})
    field_names: set = set()
    for r in proj_rows:
        try:
            data = json.loads(r["data_json"]) if r["data_json"] else {}
        except json.JSONDecodeError:
            continue
        field_names.update((data.get("fields") or {}).keys())
    phases = [
        r["phase"]
        for r in conn.execute(
            "SELECT DISTINCT phase FROM trace_projections WHERE system_id = ? ORDER BY phase",
            (system_id,),
        ).fetchall()
    ]
    return ProposalContext(
        components=components,
        entity_types=entity_types,
        projection_names=projection_names,
        field_names=sorted(field_names),
        phases=phases,
    )


def _context_block(intent: str, ctx: ProposalContext) -> str:
    return (
        f"Intent:\n{intent}\n\n"
        f"Available components: {ctx.components}\n"
        f"Available entity types: {ctx.entity_types}\n"
        f"Available projection names: {ctx.projection_names}\n"
        f"Available projection field names: {ctx.field_names}\n"
        f"Available phases: {ctx.phases}\n"
    )


def _mock_spec(ctx: ProposalContext) -> Dict[str, Any]:
    """Deterministic smoke spec built from the real context (never a model)."""
    select: List[Dict[str, str]] = [{"name": "trace", "path": "$.trace_id"}]
    spec: Dict[str, Any] = {"source": "trace_projections", "select": select}
    if ctx.field_names:
        fname = ctx.field_names[0]
        select.insert(0, {"name": fname, "path": f"$.fields.{fname}"})
        spec["group_by"] = [fname]
    if ctx.components:
        spec["filter"] = {"components": [ctx.components[0]]}
    return spec


def _extract_field_refs(spec: Dict[str, Any]) -> List[str]:
    """Collect projection field names referenced by select paths ($.fields.X)."""
    refs: List[str] = []
    for item in spec.get("select") or []:
        path = item.get("path", "") if isinstance(item, dict) else ""
        if path.startswith("$.fields."):
            rest = path[len("$.fields."):]
            name = rest.split(".", 1)[0].split("[", 1)[0]
            if name:
                refs.append(name)
    return refs


def _existence_errors(spec: Dict[str, Any], ctx: ProposalContext) -> List[str]:
    """Deterministic check against the finite set of real identifiers.

    No 'nearest name' correction — an unknown identifier is a hard reject.
    """
    errors: List[str] = []
    f = spec.get("filter") or {}
    for comp in f.get("components") or []:
        if comp not in ctx.components:
            errors.append(f"unknown component {comp!r}")
    ent = f.get("entity")
    if isinstance(ent, dict) and ent.get("type") not in ctx.entity_types:
        errors.append(f"unknown entity type {ent.get('type')!r}")
    pname = f.get("projection_name")
    if pname is not None and pname not in ctx.projection_names:
        errors.append(f"unknown projection name {pname!r}")
    for ref in _extract_field_refs(spec):
        if ref not in ctx.field_names:
            errors.append(f"unknown projection field {ref!r}")
    # compare references must resolve too: a typo'd compare field (or entity
    # type) is not caught by select-path validation, and at run time a field
    # absent from both phases aggregates as "no diff" — silently defeating the
    # fail-closed review gate. Reject unknown identifiers here instead.
    cmp = spec.get("compare")
    if isinstance(cmp, dict):
        for cfield in cmp.get("fields") or []:
            if cfield not in ctx.field_names:
                errors.append(f"unknown compare field {cfield!r}")
        cet = cmp.get("entity_type")
        if cet is not None and cet not in ctx.entity_types:
            errors.append(f"unknown compare entity type {cet!r}")
    return errors


def _strip_fence(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return cleaned


def propose(
    client: LLMClient,
    config: LLMConfig,
    intent: str,
    ctx: ProposalContext,
) -> ProposalResult:
    """Produce a validated spec, or a ProposalResult with an error (fail closed)."""
    if isinstance(client, MockLLMClient):
        # Explicit smoke path: deterministic, clearly marked mock.
        return ProposalResult(
            provider="mock", model="mock", is_mock=True, spec=_mock_spec(ctx)
        )

    if not (intent or "").strip():
        return ProposalResult(
            provider=config.provider, model=config.model, is_mock=False,
            error="intent is required",
        )

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _context_block(intent, ctx)},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
    except LLMError as exc:
        return ProposalResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=str(exc),
        )

    try:
        spec = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as exc:
        return ProposalResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=f"model output was not valid JSON: {exc}",
        )
    if not isinstance(spec, dict):
        return ProposalResult(
            provider=config.provider, model=config.model, is_mock=False,
            error="model output was not a JSON object",
        )

    try:
        compile_spec(spec)
    except AnalyzerSpecError as exc:
        return ProposalResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=f"spec failed schema validation: {exc}",
        )

    existence = _existence_errors(spec, ctx)
    if existence:
        return ProposalResult(
            provider=config.provider, model=config.model, is_mock=False,
            error="spec references unknown identifiers: " + "; ".join(existence),
        )

    return ProposalResult(
        provider=config.provider, model=config.model, is_mock=False, spec=spec
    )
