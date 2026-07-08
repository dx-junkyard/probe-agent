"""Probe Pattern reconciliation against the latest snapshot (Issue #168).

Splits classification per Principle 6:

- Deterministic structural checks decide ``exact_match`` (same path+symbol,
  same extracted signature), ``changed_signature`` (same path+symbol,
  different signature), ``unsafe`` (safety-denylist hit), and verbatim
  relocation (identical normalized body hash at exactly one new location).
- Everything open-ended — did the symbol move, get renamed, split, merge, or
  disappear — is a reasoning-model decision with evidence and a short
  confirmation question. Deterministic candidate retrieval only feeds the
  model; it never becomes the final answer, and there is no heuristic
  fallback when the model is unavailable or returns invalid output.

probe-agent:
  role: Classify saved probe pattern points against the latest snapshot
  capability: probe-pattern-lifecycle
  element_type: core
  operation_kind: analysis
  consumers: [control-server, dashboard]
  state_effects: [external-api]
  probe_value: Verify deterministic classifications never call the LLM, reasoning classifications validate targets against the symbol index, and failures never degrade into heuristic guesses.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from .patch_generator import _find_function_node
from .probe_planner import check_denylist

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"

RECONCILE_CLASSIFICATIONS = (
    "exact_match",
    "moved_match",
    "changed_signature",
    "split_or_merged",
    "missing",
    "unsafe",
)

_REASONING_CLASSIFICATIONS = (
    "moved_match",
    "changed_signature",
    "split_or_merged",
    "missing",
)


class ReconcileValidationError(ValueError):
    pass


@dataclass
class PatternPointFacts:
    """Structural facts captured when the pattern point was saved."""

    pattern_point_id: int
    path: str
    symbol: str
    component_id: str
    reason: str
    recommended_mode: str
    signature: str
    symbol_source_hash: Optional[str]
    symbol_body_hash: Optional[str]
    docstring: Optional[str]
    objective: str = ""


@dataclass
class SnapshotSymbol:
    """A symbol row from the latest snapshot's deterministic index."""

    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    docstring: Optional[str]
    symbol_source_hash: Optional[str]
    symbol_body_hash: Optional[str]


@dataclass
class EvidenceRef:
    path: str
    start_line: int
    end_line: int
    summary: str


@dataclass
class ReconcilePointResult:
    pattern_point_id: int
    classification: str
    decision_method: str  # "deterministic" | "reasoning_llm"
    target_path: Optional[str] = None
    target_symbol: Optional[str] = None
    target_line_start: Optional[int] = None
    target_line_end: Optional[int] = None
    confidence: float = 0.0
    explanation: str = ""
    hypothesis: str = ""
    question: str = ""
    evidence: List[EvidenceRef] = field(default_factory=list)
    denylist_hit: Optional[str] = None
    body_changed: bool = False


def extract_signature(source: str, symbol: str) -> str:
    """Deterministic signature string ``(args) -> returns`` for a symbol."""
    result = _find_function_node(source, symbol)
    if result is None:
        return ""
    node, _dec_line = result
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    try:
        args = ast.unparse(node.args)
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    except Exception:
        return ""
    return f"({args}){returns}"


def classify_deterministic(
    point: PatternPointFacts,
    symbols_by_key: Dict[Tuple[str, str], SnapshotSymbol],
    symbols_by_body_hash: Dict[str, List[SnapshotSymbol]],
    file_sources: Dict[str, str],
) -> Optional[ReconcilePointResult]:
    """Classify a point with structural checks only; None means undecidable.

    Decidable cases form an explicit finite set: the symbol still exists at
    the same path (compare signature/denylist), or its normalized body hash
    reappears at exactly one other location (verbatim move).
    """
    current = symbols_by_key.get((point.path, point.symbol))
    if current is not None:
        denylist = check_denylist(point.symbol, current.docstring)
        if denylist:
            return ReconcilePointResult(
                pattern_point_id=point.pattern_point_id,
                classification="unsafe",
                decision_method="deterministic",
                target_path=current.path,
                target_symbol=current.qualified_name,
                target_line_start=current.start_line,
                target_line_end=current.end_line,
                confidence=1.0,
                explanation=(
                    "Symbol still exists but hits the safety denylist: "
                    f"{denylist}"
                ),
                denylist_hit=denylist,
                evidence=[EvidenceRef(
                    path=current.path,
                    start_line=current.start_line,
                    end_line=current.end_line,
                    summary="Current definition of the probed symbol",
                )],
            )

        new_signature = extract_signature(
            file_sources.get(current.path, ""), point.symbol,
        )
        body_changed = bool(
            point.symbol_body_hash
            and current.symbol_body_hash
            and point.symbol_body_hash != current.symbol_body_hash
        )
        if point.signature and new_signature and point.signature != new_signature:
            return ReconcilePointResult(
                pattern_point_id=point.pattern_point_id,
                classification="changed_signature",
                decision_method="deterministic",
                target_path=current.path,
                target_symbol=current.qualified_name,
                target_line_start=current.start_line,
                target_line_end=current.end_line,
                confidence=1.0,
                explanation=(
                    f"Signature changed from {point.signature} "
                    f"to {new_signature}"
                ),
                question=(
                    "The interface of this symbol changed. Re-attach the "
                    "probe to the new signature?"
                ),
                body_changed=body_changed,
                evidence=[EvidenceRef(
                    path=current.path,
                    start_line=current.start_line,
                    end_line=current.end_line,
                    summary=f"Current signature: {new_signature}",
                )],
            )
        return ReconcilePointResult(
            pattern_point_id=point.pattern_point_id,
            classification="exact_match",
            decision_method="deterministic",
            target_path=current.path,
            target_symbol=current.qualified_name,
            target_line_start=current.start_line,
            target_line_end=current.end_line,
            confidence=1.0,
            explanation=(
                "Symbol exists at the same path with the same signature"
                + ("; body has changed since capture" if body_changed else "")
            ),
            body_changed=body_changed,
            evidence=[EvidenceRef(
                path=current.path,
                start_line=current.start_line,
                end_line=current.end_line,
                summary="Current definition of the probed symbol",
            )],
        )

    # Verbatim relocation: identical normalized AST at exactly one location.
    if point.symbol_body_hash:
        matches = symbols_by_body_hash.get(point.symbol_body_hash, [])
        if len(matches) == 1:
            target = matches[0]
            denylist = check_denylist(target.qualified_name, target.docstring)
            return ReconcilePointResult(
                pattern_point_id=point.pattern_point_id,
                classification="unsafe" if denylist else "moved_match",
                decision_method="deterministic",
                target_path=target.path,
                target_symbol=target.qualified_name,
                target_line_start=target.start_line,
                target_line_end=target.end_line,
                confidence=1.0,
                explanation=(
                    "Identical implementation (normalized AST hash) found at "
                    f"{target.path}:{target.qualified_name}"
                ),
                question=(
                    "This function moved verbatim to "
                    f"{target.path}:{target.qualified_name}. "
                    "Move the probe point there?"
                ),
                denylist_hit=denylist,
                evidence=[EvidenceRef(
                    path=target.path,
                    start_line=target.start_line,
                    end_line=target.end_line,
                    summary="Verbatim relocation of the probed symbol",
                )],
            )

    return None


def build_candidates(
    point: PatternPointFacts,
    symbols: List[SnapshotSymbol],
    file_sources: Dict[str, str],
    limit: int = 15,
) -> List[SnapshotSymbol]:
    """Deterministic retrieval of candidate symbols for the reasoning model.

    Retrieval only — same terminal name, same file, or same directory. The
    candidates constrain what the model may reference; they never decide.
    """
    terminal = point.symbol.split(".")[-1]
    directory = point.path.rsplit("/", 1)[0] if "/" in point.path else ""
    ranked: List[Tuple[int, SnapshotSymbol]] = []
    for sym in symbols:
        if sym.kind not in ("function", "async_function"):
            continue
        score = 0
        if sym.qualified_name.split(".")[-1] == terminal:
            score += 3
        if sym.path == point.path:
            score += 2
        elif directory and sym.path.startswith(directory + "/"):
            score += 1
        if score > 0:
            ranked.append((score, sym))
    ranked.sort(key=lambda item: (-item[0], item[1].path, item[1].qualified_name))
    return [sym for _score, sym in ranked[:limit]]


_SYSTEM_PROMPT = """\
You reconcile previously saved probe observation points against the current
state of a codebase. For each old probe point you decide whether it moved,
changed interface, was split or merged, or no longer exists. You must ground
every decision in the provided candidate symbols and evidence, state a short
hypothesis, and ask one short confirmation question a developer can answer
with yes/no."""

_RECONCILE_PROMPT_TEMPLATE = """\
A developer saved probe points (runtime observation targets) before removing
them from production. The code has changed since. Classify each unresolved
point against the CURRENT snapshot.

Observation objective: {objective}

Unresolved probe points (old state):
{points_context}

Candidate symbols in the CURRENT snapshot (retrieval hints only):
{candidates_context}

For each point, respond with:
- pattern_point_id: integer id from the list above
- classification: one of "moved_match" (same responsibility at a new
  path/symbol), "changed_signature" (same role, interface changed),
  "split_or_merged" (responsibility split across or merged into other
  symbols), "missing" (no current counterpart)
- targets: array of {{"path": "...", "symbol": "..."}} — MUST be chosen from
  the candidate symbols above; empty array for "missing"
- hypothesis: one or two short sentences stating what you think happened
- explanation: why, grounded in the evidence
- question: ONE short confirmation question answerable with yes/no
- confidence: 0.0-1.0
- evidence: array of {{"path": "...", "start_line": n, "end_line": n,
  "summary": "..."}} referencing current snapshot files

Respond with ONLY valid JSON:
{{
  "points": [
    {{
      "pattern_point_id": 1,
      "classification": "moved_match",
      "targets": [{{"path": "a/b.py", "symbol": "new_func"}}],
      "hypothesis": "string",
      "explanation": "string",
      "question": "string",
      "confidence": 0.8,
      "evidence": [{{"path": "a/b.py", "start_line": 1, "end_line": 5, "summary": "string"}}]
    }}
  ]
}}"""


def _points_context(points: List[PatternPointFacts]) -> str:
    parts = []
    for p in points:
        doc = f' doc="{p.docstring[:120]}"' if p.docstring else ""
        parts.append(
            f"- id={p.pattern_point_id} {p.path}:{p.symbol} "
            f"signature={p.signature or 'unknown'} mode={p.recommended_mode}\n"
            f"  Reason for probing: {p.reason or 'not recorded'}{doc}"
        )
    return "\n".join(parts)


def _candidates_context(
    candidates_by_point: Dict[int, List[SnapshotSymbol]],
    file_sources: Dict[str, str],
) -> str:
    parts = []
    seen = set()
    for point_id, candidates in candidates_by_point.items():
        parts.append(f"For point id={point_id}:")
        if not candidates:
            parts.append("  (no structural candidates found)")
        for sym in candidates:
            key = (sym.path, sym.qualified_name)
            signature = extract_signature(
                file_sources.get(sym.path, ""), sym.qualified_name,
            )
            doc = f' doc="{sym.docstring[:100]}"' if sym.docstring else ""
            dup = " (listed above)" if key in seen else ""
            seen.add(key)
            parts.append(
                f"  - {sym.path}:{sym.qualified_name} "
                f"lines {sym.start_line}-{sym.end_line} "
                f"signature={signature or 'unknown'}{doc}{dup}"
            )
    return "\n".join(parts)


def _strip_code_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return cleaned


def _parse_evidence(items, known_paths) -> List[EvidenceRef]:
    evidence = []
    if not isinstance(items, list):
        raise ReconcileValidationError("evidence must be an array")
    for item in items:
        if not isinstance(item, dict):
            raise ReconcileValidationError("evidence items must be objects")
        path = str(item.get("path", "")).strip()
        if path not in known_paths:
            raise ReconcileValidationError(
                f"evidence references unknown snapshot path: {path}"
            )
        try:
            start = int(item.get("start_line", 0))
            end = int(item.get("end_line", 0))
        except (TypeError, ValueError):
            raise ReconcileValidationError("evidence lines must be integers")
        evidence.append(EvidenceRef(
            path=path,
            start_line=max(start, 0),
            end_line=max(end, 0),
            summary=str(item.get("summary", "")).strip(),
        ))
    return evidence


def parse_reconcile_response(
    raw_json: str,
    expected_point_ids: List[int],
    symbols_by_key: Dict[Tuple[str, str], SnapshotSymbol],
    known_paths,
) -> List[ReconcilePointResult]:
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise ReconcileValidationError("LLM response must be an object")
    points_data = data.get("points", [])
    if not isinstance(points_data, list):
        raise ReconcileValidationError("points must be an array")

    results: Dict[int, ReconcilePointResult] = {}
    for item in points_data:
        if not isinstance(item, dict):
            raise ReconcileValidationError("point items must be objects")
        try:
            point_id = int(item.get("pattern_point_id"))
        except (TypeError, ValueError):
            raise ReconcileValidationError("pattern_point_id must be an integer")
        if point_id not in expected_point_ids:
            raise ReconcileValidationError(
                f"unexpected pattern_point_id: {point_id}"
            )
        if point_id in results:
            raise ReconcileValidationError(
                f"duplicate pattern_point_id: {point_id}"
            )

        classification = str(item.get("classification", "")).strip()
        if classification not in _REASONING_CLASSIFICATIONS:
            raise ReconcileValidationError(
                f"invalid classification: {classification}"
            )

        targets_data = item.get("targets", [])
        if not isinstance(targets_data, list):
            raise ReconcileValidationError("targets must be an array")
        targets: List[SnapshotSymbol] = []
        for t in targets_data:
            if not isinstance(t, dict):
                raise ReconcileValidationError("target items must be objects")
            key = (str(t.get("path", "")).strip(), str(t.get("symbol", "")).strip())
            sym = symbols_by_key.get(key)
            if sym is None:
                raise ReconcileValidationError(
                    f"target is not an indexed symbol: {key[0]}:{key[1]}"
                )
            targets.append(sym)
        if classification == "missing" and targets:
            raise ReconcileValidationError(
                "missing classification must not have targets"
            )
        if classification in ("moved_match", "changed_signature") and len(targets) != 1:
            raise ReconcileValidationError(
                f"{classification} requires exactly one target"
            )
        if classification == "split_or_merged" and not targets:
            raise ReconcileValidationError(
                "split_or_merged requires at least one target"
            )

        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(max(confidence, 0.0), 1.0)

        question = str(item.get("question", "")).strip()
        hypothesis = str(item.get("hypothesis", "")).strip()
        if classification != "missing" and not question:
            raise ReconcileValidationError(
                "question is required for non-missing classifications"
            )

        evidence = _parse_evidence(item.get("evidence", []), known_paths)
        for target in targets[1:]:
            evidence.append(EvidenceRef(
                path=target.path,
                start_line=target.start_line,
                end_line=target.end_line,
                summary=f"Additional target: {target.qualified_name}",
            ))

        primary = targets[0] if targets else None
        denylist = (
            check_denylist(primary.qualified_name, primary.docstring)
            if primary is not None
            else None
        )
        results[point_id] = ReconcilePointResult(
            pattern_point_id=point_id,
            classification="unsafe" if denylist else classification,
            decision_method="reasoning_llm",
            target_path=primary.path if primary else None,
            target_symbol=primary.qualified_name if primary else None,
            target_line_start=primary.start_line if primary else None,
            target_line_end=primary.end_line if primary else None,
            confidence=confidence,
            explanation=str(item.get("explanation", "")).strip(),
            hypothesis=hypothesis,
            question=question,
            evidence=evidence,
            denylist_hit=denylist,
        )

    unresolved = [pid for pid in expected_point_ids if pid not in results]
    if unresolved:
        raise ReconcileValidationError(
            f"LLM response missing classifications for point ids: {unresolved}"
        )
    return [results[pid] for pid in expected_point_ids]


def classify_with_reasoning(
    client: LLMClient,
    config: LLMConfig,
    objective: str,
    points: List[PatternPointFacts],
    symbols: List[SnapshotSymbol],
    file_sources: Dict[str, str],
) -> List[ReconcilePointResult]:
    """Reasoning-model classification for points deterministic checks left open.

    Raises LLMError / ReconcileValidationError on any failure — callers must
    fail the reconciliation run instead of guessing.
    """
    if isinstance(client, MockLLMClient):
        raise LLMError(
            "Pattern reconciliation requires a real reasoning model; "
            "mock/heuristic fallback is prohibited"
        )

    candidates_by_point = {
        p.pattern_point_id: build_candidates(p, symbols, file_sources)
        for p in points
    }
    prompt = _RECONCILE_PROMPT_TEMPLATE.format(
        objective=objective.strip() or "not recorded",
        points_context=_points_context(points),
        candidates_context=_candidates_context(candidates_by_point, file_sources),
    )
    raw = client.generate_text(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=8192,
    )

    symbols_by_key = {(s.path, s.qualified_name): s for s in symbols}
    try:
        return parse_reconcile_response(
            _strip_code_fences(raw),
            [p.pattern_point_id for p in points],
            symbols_by_key,
            set(file_sources.keys()),
        )
    except json.JSONDecodeError as exc:
        raise ReconcileValidationError(f"invalid JSON from LLM: {exc}")


# ---------------------------------------------------------------------------
# Investigation assistance ("わからないので調べる")
# ---------------------------------------------------------------------------

INVESTIGATE_PROMPT_VERSION = "v1"

_INVESTIGATE_SYSTEM_PROMPT = """\
You help a developer who answered "I don't know" to a probe reconciliation
question. Read the provided current implementation excerpts and summarize, in
a few short sentences, what the current implementation actually does around
the old observation target, then recommend where (or whether) to re-attach
the probe. Be concrete, cite paths, and keep it short."""

_INVESTIGATE_PROMPT_TEMPLATE = """\
Old probe point: {path}:{symbol} (signature {signature})
Reason it was probed: {reason}
Observation objective: {objective}
Current reconcile hypothesis: {hypothesis}

Current implementation excerpts:
{excerpts}

Respond with ONLY valid JSON:
{{
  "summary": "2-4 short sentences describing the current implementation state relevant to this probe",
  "recommendation": "one short sentence: where to put the probe, or why it should not be re-attached",
  "proposed_target": {{"path": "...", "symbol": "..."}} or null,
  "evidence": [{{"path": "...", "start_line": 1, "end_line": 5, "summary": "..."}}]
}}"""


@dataclass
class InvestigationResult:
    summary: str
    recommendation: str
    proposed_target_path: Optional[str] = None
    proposed_target_symbol: Optional[str] = None
    evidence: List[EvidenceRef] = field(default_factory=list)


def investigate_point(
    client: LLMClient,
    config: LLMConfig,
    point: PatternPointFacts,
    hypothesis: str,
    excerpts: List[Tuple[str, str]],
    symbols_by_key: Dict[Tuple[str, str], SnapshotSymbol],
    known_paths,
) -> InvestigationResult:
    """Scan related implementation and produce a short state summary.

    ``excerpts`` are (path, content) pairs read from the pinned snapshot only.
    Raises on any LLM or validation failure — no heuristic fallback.
    """
    if isinstance(client, MockLLMClient):
        raise LLMError(
            "Investigation requires a real reasoning model; "
            "mock/heuristic fallback is prohibited"
        )

    excerpt_parts = []
    for path, content in excerpts:
        excerpt_parts.append(f"--- {path} ---\n{content}")
    prompt = _INVESTIGATE_PROMPT_TEMPLATE.format(
        path=point.path,
        symbol=point.symbol,
        signature=point.signature or "unknown",
        reason=point.reason or "not recorded",
        objective=point.objective or "not recorded",
        hypothesis=hypothesis or "none",
        excerpts="\n\n".join(excerpt_parts) or "(no excerpts available)",
    )
    raw = client.generate_text(
        [
            {"role": "system", "content": _INVESTIGATE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=4096,
    )

    try:
        data = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise ReconcileValidationError(f"invalid JSON from LLM: {exc}")
    if not isinstance(data, dict):
        raise ReconcileValidationError("LLM response must be an object")

    summary = str(data.get("summary", "")).strip()
    recommendation = str(data.get("recommendation", "")).strip()
    if not summary or not recommendation:
        raise ReconcileValidationError(
            "summary and recommendation are required"
        )

    proposed_path = None
    proposed_symbol = None
    proposed = data.get("proposed_target")
    if proposed is not None:
        if not isinstance(proposed, dict):
            raise ReconcileValidationError("proposed_target must be an object")
        key = (
            str(proposed.get("path", "")).strip(),
            str(proposed.get("symbol", "")).strip(),
        )
        if key not in symbols_by_key:
            raise ReconcileValidationError(
                f"proposed_target is not an indexed symbol: {key[0]}:{key[1]}"
            )
        proposed_path, proposed_symbol = key

    evidence = _parse_evidence(data.get("evidence", []), known_paths)
    return InvestigationResult(
        summary=summary,
        recommendation=recommendation,
        proposed_target_path=proposed_path,
        proposed_target_symbol=proposed_symbol,
        evidence=evidence,
    )
