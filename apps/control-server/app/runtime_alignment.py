"""Deterministic claim <-> runtime-fact match evaluation (Issue #290).

Two separate, deterministic pieces (Principle 6 -- both are structural
checks, never a semantic judgement of whether *behavior* matches):

- ``resolve_component_for_evidence``: maps a set of evidence citations
  (path/start_line/end_line, e.g. an alignment item's ``current_evidence``)
  to AT MOST one ``component_id``, via the same ``code_symbols`` index
  Feature-to-Code mapping already populates. A citation range that overlaps
  more than one distinct ``component_id`` -- or none at all -- resolves to
  ``None``: this function never guesses between candidates.
- ``compare_claim_to_runtime``: given a fact + its provenance envelope
  (``app/runtime_reality.py``), returns one of the finite
  ``match | mismatch | unobserved | stale`` states using ONLY structural
  signals -- presence/absence of trace data, freshness, and (when both the
  fact and an expected environment are known) environment equality. The
  ``claim`` text itself is accepted for interface/audit completeness but is
  never parsed or inspected here: judging whether a claim's free-text
  *meaning* matches observed behavior is a semantic call that belongs to the
  reasoning-model Investigation Agent (Issue #286), which must express that
  judgement through this same finite vocabulary (schema-validated), not to
  this deterministic function.

probe-agent:
  role: Deterministic component-evidence mapping + finite claim/runtime match
    state (match/mismatch/unobserved/stale)
  capability: interactive-system-understanding
  element_type: element
  consumers: [interview-alignment-routes, investigation-agent]
  operation_kind: analysis
  state_effects: [database-read]
  probe_value: Verify compare_claim_to_runtime never returns 'match' for stale/unobserved facts, and that resolve_component_for_evidence returns None (never a guess) when evidence maps to zero or multiple distinct components.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Optional

from .models import RuntimeFactProvenanceOut, RuntimeTraceFactsOut

RUNTIME_CHECK_STATES = ("match", "mismatch", "unobserved", "stale")


def resolve_component_for_evidence(
    conn: sqlite3.Connection,
    snapshot_id: int,
    evidence: Iterable[Dict[str, object]],
) -> Optional[str]:
    """Deterministic evidence(path/line range) -> component_id mapping.

    Looks up ``code_symbols`` rows for ``snapshot_id`` whose ``path`` matches
    an evidence citation and whose ``[start_line, end_line]`` overlaps the
    citation's range, restricted to rows with a non-null ``component_id``
    (Issue #24's Feature-to-Code index). Returns the single distinct
    ``component_id`` found across every citation, or ``None`` when zero or
    more than one distinct ``component_id`` is found -- an ambiguous or
    absent mapping is never guessed (Principle 6).
    """
    found: set = set()
    for item in evidence:
        path = item.get("path") if isinstance(item, dict) else getattr(item, "path", None)
        start_line = (
            item.get("start_line") if isinstance(item, dict) else getattr(item, "start_line", None)
        )
        end_line = (
            item.get("end_line") if isinstance(item, dict) else getattr(item, "end_line", None)
        )
        if not path or start_line is None or end_line is None:
            continue
        rows = conn.execute(
            """SELECT DISTINCT component_id FROM code_symbols
               WHERE snapshot_id = ? AND path = ? AND component_id IS NOT NULL
                 AND start_line <= ? AND end_line >= ?""",
            (snapshot_id, path, end_line, start_line),
        ).fetchall()
        for row in rows:
            found.add(row["component_id"])
        if len(found) > 1:
            return None
    if len(found) == 1:
        return next(iter(found))
    return None


def compare_claim_to_runtime(
    claim: str,
    fact: RuntimeTraceFactsOut,
    provenance: RuntimeFactProvenanceOut,
    *,
    expected_environment: Optional[str] = None,
) -> str:
    """Deterministic finite match state for one (claim, fact) pair.

    ``claim`` is accepted for call-site/audit clarity only and is never
    inspected: every branch below reads only ``fact``/``provenance`` finite
    fields (Principle 6). First-match order:

    1. ``provenance.freshness == 'unobserved'`` -> 'unobserved' (no trace
       data at all for the mapped component).
    2. ``provenance.freshness == 'stale'`` -> 'stale' (data exists but is
       older than ``RUNTIME_FACT_FRESH_SECONDS``; never presented as
       current, per the Issue #290 stale guard).
    3. Both ``expected_environment`` and ``provenance.environment`` are
       known and differ -> 'mismatch' (the one deterministic negative
       signal the brief enumerates; today ``provenance.environment`` is
       always None since the ``traces`` table carries no environment
       column, so this branch is unreachable with real data until that
       capability exists -- exercised in tests via a constructed
       provenance).
    4. Otherwise -> 'match' (fresh data, no structural conflict detected).
    """
    del claim  # intentionally unused -- see docstring
    if provenance.freshness == "unobserved":
        return "unobserved"
    if provenance.freshness == "stale":
        return "stale"
    if (
        expected_environment
        and provenance.environment
        and provenance.environment != expected_environment
    ):
        return "mismatch"
    return "match"
