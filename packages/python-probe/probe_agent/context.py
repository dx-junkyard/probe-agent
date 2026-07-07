"""Runtime lineage context for probes (Issue #145, Phase 1).

Provides ``probe_context`` and ``add_entity`` so that a set of probe
invocations can share a ``correlation_id`` / ``flow_id`` and carry explicit
business entities. All values are optional and stored in ``contextvars`` so
they propagate through normal (synchronous) call nesting.

Design notes:
  * These helpers add no state to a probe when it is ``off`` / disabled — the
    decorator only reads the context variables after its early-return checks.
  * ``threading.Thread`` does NOT inherit contextvars automatically. The
    shadow path copies the context explicitly (``contextvars.copy_context``)
    so a candidate's nested probes stay on the same lineage.
"""

import contextlib
import contextvars
import uuid
from typing import Any, Dict, Iterator, List, Optional

_ENTITY_ROLES = ("source", "derived", "related")

_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "probe_correlation_id", default=None
)
_flow_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "probe_flow_id", default=None
)
_current_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "probe_current_span_id", default=None
)
_entities: contextvars.ContextVar[Optional[List[Dict[str, str]]]] = contextvars.ContextVar(
    "probe_entities", default=None
)


def new_span_id() -> str:
    return uuid.uuid4().hex


def _normalize_entity(entity: Any) -> Optional[Dict[str, str]]:
    """Coerce a caller-supplied entity into ``{type, id, role}``.

    Returns ``None`` (dropped) if the entity is missing a type or id, so a
    malformed entity never breaks tracing. Unknown roles fall back to
    ``related`` (the finite enum is enforced deterministically).
    """
    if isinstance(entity, dict):
        etype = entity.get("type")
        eid = entity.get("id")
        role = entity.get("role", "related")
    else:  # tuple/list style (type, id[, role])
        try:
            etype = entity[0]
            eid = entity[1]
            role = entity[2] if len(entity) > 2 else "related"
        except (TypeError, IndexError):
            return None
    if etype is None or eid is None:
        return None
    if role not in _ENTITY_ROLES:
        role = "related"
    return {"type": str(etype), "id": str(eid), "role": role}


def _normalize_entities(entities: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    if not entities:
        return result
    for e in entities:
        norm = _normalize_entity(e)
        if norm is not None:
            result.append(norm)
    return result


@contextlib.contextmanager
def probe_context(
    correlation_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    entities: Optional[List[Any]] = None,
) -> Iterator[None]:
    """Bind a lineage context for all probes executed inside the block.

    ``correlation_id`` / ``flow_id`` are inherited from an enclosing context
    when not provided, and otherwise generated so that every probe inside the
    block shares the same ids. ``entities`` are appended to any inherited
    entities and apply to every probe in the block (explicit values only).
    """
    parent_corr = _correlation_id.get()
    parent_flow = _flow_id.get()
    corr = correlation_id or parent_corr or new_span_id()
    flow = flow_id or parent_flow or new_span_id()

    merged = list(_entities.get() or [])
    merged.extend(_normalize_entities(entities))

    t_corr = _correlation_id.set(corr)
    t_flow = _flow_id.set(flow)
    t_ent = _entities.set(merged)
    try:
        yield
    finally:
        _entities.reset(t_ent)
        _flow_id.reset(t_flow)
        _correlation_id.reset(t_corr)


def add_entity(type: str, id: str, role: str = "related") -> None:
    """Attach an explicit entity to the current lineage context.

    Applies to probe invocations that run afterwards in the same context.
    Values are supplied by the caller; no extraction is performed here
    (path-based extraction is Phase 2 / Issue #146).
    """
    norm = _normalize_entity({"type": type, "id": id, "role": role})
    if norm is None:
        return
    current = _entities.get()
    if current is None:
        current = []
        _entities.set(current)
    current.append(norm)


def current_lineage(extra_entities: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Snapshot the active lineage for a probe invocation.

    Returns only the keys that carry a value plus a freshly minted
    ``span_id``; the caller merges this into the trace payload.
    """
    span_id = new_span_id()
    lineage: Dict[str, Any] = {"span_id": span_id}
    parent = _current_span_id.get()
    if parent:
        lineage["parent_span_id"] = parent
    corr = _correlation_id.get()
    if corr:
        lineage["correlation_id"] = corr
    flow = _flow_id.get()
    if flow:
        lineage["flow_id"] = flow

    entities = list(_entities.get() or [])
    entities.extend(_normalize_entities(extra_entities))
    if entities:
        lineage["entities"] = entities
    return lineage


def enter_span(span_id: str) -> contextvars.Token:
    """Mark ``span_id`` as the current span; nested probes become its children."""
    return _current_span_id.set(span_id)


def exit_span(token: contextvars.Token) -> None:
    _current_span_id.reset(token)
