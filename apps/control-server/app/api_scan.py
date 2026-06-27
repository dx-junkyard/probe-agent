"""LLM-assisted, framework-agnostic API definition discovery.

The deterministic AST indexer (``entrypoint_discovery``) only recognizes
FastAPI/Starlette/Flask. For every other framework or language (Django/DRF,
Express, NestJS, Go, Rails, aiohttp, ...) it finds no routes.

This module adds the Repository "Scan API definitions" capability the user
asked for: a reasoning model looks at a bounded digest of the pinned snapshot,
decides *where* API definitions live, and returns **regular expressions that
filter API definitions**. The regexes are then applied deterministically to the
snapshot to extract concrete ``(method, path, file, line)`` entrypoints.

This honours the codebase rules:
- The LLM makes the open-ended judgment (which files define APIs and what regex
  identifies them). The regex is the deterministic filter (CLAUDE.md principle 6
  / reasoning-llm skill).
- Mock and non-reasoning models fail closed; there is no heuristic fallback.
- The generated regexes are reviewable artifacts persisted alongside an audit
  run, kept separate from deterministic AST facts.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from .flow_graph import EvidenceRef, FlowEntrypoint
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "api-scan-v1"
SCHEMA_VERSION = "api-scan-v1"

# Safety bounds for applying untrusted (model-authored) regexes. Matching is
# done line-by-line against capped-length lines so worst-case backtracking is
# bounded by a single short line rather than a whole file.
MAX_REGEX_LEN = 256
MAX_LINE_LEN = 2000
MAX_MATCHES_PER_PATTERN = 2000
MAX_PATTERNS = 40
MAX_EXTRACTED_ENTRYPOINTS = 1000

# Lightweight rejection of the classic catastrophic-backtracking shapes
# (nested quantifiers). Retrieval heuristic only; not a final decision.
_REDOS_SIGNATURES = [
    re.compile(r"\([^)]*[+*][^)]*\)[+*]"),   # (a+)+ / (a*)* / (..|..)+ ...
    re.compile(r"\([^)]*\)\{\d+,\}[+*]"),    # (..){2,}+
]


def _digest_budget() -> int:
    try:
        return int(os.getenv("API_SCAN_DIGEST_MAX_CHARS", "40000"))
    except ValueError:
        return 40000


# Path/name hints used only to *prioritize* which files get content samples in
# the digest. The model still makes the final decision over the full inventory.
_ROUTE_HINTS = (
    "route", "router", "controller", "handler", "endpoint", "api", "urls",
    "url", "server", "app", "main", "views", "view", "resource", "resources",
    "rest", "graphql", "rpc", "service",
)
_CODE_EXTS = (
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rb", ".java",
    ".kt", ".php", ".rs", ".cs", ".scala", ".ex", ".exs", ".clj",
)


@dataclass
class ApiScanPattern:
    file_glob: str
    regex: str
    reason: str
    confidence: float
    framework: str
    language: str
    method_group: Optional[str] = None
    path_group: Optional[str] = None
    method_constant: Optional[str] = None
    examples: List[Tuple[str, int]] = field(default_factory=list)


@dataclass
class ApiScanResult:
    provider: str
    model: str
    is_mock: bool
    patterns: List[ApiScanPattern] = field(default_factory=list)
    error: Optional[str] = None


class ApiScanValidationError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Snapshot digest (deterministic, token-bounded context for the model)
# ---------------------------------------------------------------------------


def _ext(path: str) -> str:
    base = os.path.basename(path)
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


def _hint_score(path: str) -> int:
    lowered = path.lower()
    return sum(1 for h in _ROUTE_HINTS if h in lowered)


def build_snapshot_digest(
    files: List[Tuple[str, str]], max_chars: Optional[int] = None
) -> str:
    """Build a bounded repo digest: a full file inventory plus head samples of
    the files most likely to define API routes, capped to a char budget."""
    budget = max_chars if max_chars is not None else _digest_budget()

    inventory_lines = [f"- {p} ({len(src)} bytes)" for p, src in sorted(files)]
    inventory = "\n".join(inventory_lines)

    code_files = [(p, src) for p, src in files if _ext(p) in _CODE_EXTS]
    code_files.sort(key=lambda ps: (-_hint_score(ps[0]), ps[0]))

    samples: List[str] = []
    used = len(inventory)
    for path, src in code_files:
        head = "\n".join(src.splitlines()[:60])
        block = f"\n\n### {path}\n```\n{head}\n```"
        if used + len(block) > budget:
            continue
        samples.append(block)
        used += len(block)

    return (
        "## File inventory\n"
        f"{inventory}\n\n"
        "## Source samples (heads of likely API-defining files)"
        f"{''.join(samples)}"
    )


_SYSTEM_PROMPT = """\
You are a software analysis assistant. You locate HTTP/RPC API definitions in a
source repository and express how to find them as regular expressions. You do
not guess endpoints from names alone; every pattern must be grounded in the
actual route-declaration syntax visible in the provided source."""

_SCAN_PROMPT_TEMPLATE = """\
Analyze this repository snapshot and identify where backend API endpoints
(HTTP routes, RPC/GraphQL handlers) are DECLARED, across any framework or
language. For each distinct declaration style you find, produce a regular
expression that matches one endpoint declaration per match.

Rules:
- Base every pattern on declaration syntax actually present in the samples.
- Each regex should match a single endpoint declaration (typically one line).
- You MUST capture the URL path in a named group `(?P<path>...)` inside the `regex`. This is STRICTLY REQUIRED. If you fail to include `(?P<path>...)` in the regex, the system will crash. Set `path_group` to "path".
- If the HTTP method is part of the declaration, capture it in `(?P<method>...)` inside the `regex`; otherwise set `method_constant` (e.g. "GET") or leave it null for "ANY".
- `file_glob` must be a repository-relative glob (e.g. `app/routes/*.js`,
  `**/urls.py`) selecting the files this pattern applies to.
- Keep regexes simple and linear; avoid nested quantifiers like `(a+)+`.
- Prefer a few precise patterns over many speculative ones.

Respond with ONLY valid JSON matching this schema:
{{
  "patterns": [
    {{
      "file_glob": "string",
      "regex": "string (MUST contain (?P<path>...))",
      "method_group": "string-or-null",
      "path_group": "string (MUST be the exact name of the group, e.g. 'path')",
      "method_constant": "string-or-null",
      "framework": "string",
      "language": "string",
      "reason": "string",
      "confidence": number,
      "examples": [{{"path": "string", "line": number}}]
    }}
  ]
}}

## Repository snapshot

{digest}"""


def _looks_redos(pattern: str) -> bool:
    return any(sig.search(pattern) for sig in _REDOS_SIGNATURES)


def _strip_json_fence(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if not lines:
        return cleaned
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _load_first_json_object(raw_json: str) -> Any:
    """Load the first JSON object from an LLM response.

    The prompt requires JSON-only output, but real models can still wrap the
    object in a markdown code fence or append a short explanation after the
    object. Accepting the first valid object keeps API scan operational while
    the schema validation below remains strict.
    """
    cleaned = _strip_json_fence(raw_json)
    decoder = json.JSONDecoder()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_exc:
        start = cleaned.find("{")
        if start < 0:
            raise first_exc
        try:
            data, _ = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            raise first_exc
        return data


def parse_scan_response(raw_json: str) -> List[ApiScanPattern]:
    data = _load_first_json_object(raw_json)
    if not isinstance(data, dict):
        raise ApiScanValidationError("LLM response must be an object")
    patterns_data = data.get("patterns", [])
    if not isinstance(patterns_data, list):
        raise ApiScanValidationError("patterns must be an array")
    if len(patterns_data) > MAX_PATTERNS:
        raise ApiScanValidationError(
            f"too many patterns ({len(patterns_data)} > {MAX_PATTERNS})"
        )

    results: List[ApiScanPattern] = []
    seen = set()
    for item in patterns_data:
        if not isinstance(item, dict):
            raise ApiScanValidationError("pattern items must be objects")

        regex = str(item.get("regex", ""))
        if not regex:
            raise ApiScanValidationError("regex is required")
        if len(regex) > MAX_REGEX_LEN:
            raise ApiScanValidationError(
                f"regex exceeds {MAX_REGEX_LEN} chars"
            )
        if _looks_redos(regex):
            raise ApiScanValidationError(
                f"regex rejected as potential catastrophic backtracking: {regex}"
            )
        try:
            compiled = re.compile(regex)
        except re.error as exc:
            raise ApiScanValidationError(f"regex does not compile: {exc}") from exc

        file_glob = str(item.get("file_glob", "")).strip()
        if not file_glob:
            raise ApiScanValidationError("file_glob is required")
        if file_glob.startswith("/") or ".." in file_glob:
            raise ApiScanValidationError(
                f"file_glob must be repository-relative: {file_glob}"
            )

        method_group = item.get("method_group") or None
        path_group = item.get("path_group") or None
        method_constant = item.get("method_constant") or None
        if method_group is not None:
            method_group = str(method_group)
            if method_group not in compiled.groupindex:
                raise ApiScanValidationError(
                    f"method_group '{method_group}' is not a named group"
                )
        if path_group is not None:
            path_group = str(path_group)
            if path_group not in compiled.groupindex:
                raise ApiScanValidationError(
                    f"path_group '{path_group}' is not a named group"
                )
        if path_group is None:
            raise ApiScanValidationError(
                "path_group is required so a route path can be extracted"
            )
        if method_constant is not None:
            method_constant = str(method_constant).strip().upper()

        reason = str(item.get("reason", "")).strip()
        if not reason:
            raise ApiScanValidationError("reason is required")
        framework = str(item.get("framework", "")).strip() or "unknown"
        language = str(item.get("language", "")).strip() or "unknown"

        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError) as exc:
            raise ApiScanValidationError("confidence must be a number") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ApiScanValidationError("confidence must be between 0 and 1")

        examples: List[Tuple[str, int]] = []
        for ex in item.get("examples", []) or []:
            if isinstance(ex, dict) and ex.get("path"):
                try:
                    line = int(ex.get("line", 0))
                except (TypeError, ValueError):
                    line = 0
                examples.append((str(ex["path"]), line))

        key = (file_glob, regex)
        if key in seen:
            continue
        seen.add(key)
        results.append(ApiScanPattern(
            file_glob=file_glob,
            regex=regex,
            reason=reason,
            confidence=confidence,
            framework=framework,
            language=language,
            method_group=method_group,
            path_group=path_group,
            method_constant=method_constant,
            examples=examples,
        ))
    return results


def generate_api_scan(
    client: LLMClient,
    config: LLMConfig,
    digest: str,
) -> ApiScanResult:
    """Run the reasoning model to produce API-definition regex patterns.

    Fails closed for the mock provider: API scanning requires a real reasoning
    model, exactly like feature-to-code mapping.
    """
    if isinstance(client, MockLLMClient):
        return ApiScanResult(
            provider="mock",
            model="mock",
            is_mock=True,
            error=(
                "API definition scanning requires a real reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    prompt = _SCAN_PROMPT_TEMPLATE.format(digest=digest)
    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=8192,
        )
    except LLMError as exc:
        return ApiScanResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=str(exc),
        )

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    try:
        patterns = parse_scan_response(cleaned)
    except (json.JSONDecodeError, ApiScanValidationError, KeyError, TypeError) as exc:
        return ApiScanResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=f"Failed to parse LLM response: {exc}",
        )

    return ApiScanResult(
        provider=config.provider, model=config.model, is_mock=False,
        patterns=patterns,
    )


# ---------------------------------------------------------------------------
# Deterministic application of the model-authored regexes
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    path = path.strip()
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def apply_patterns(
    patterns: List[ApiScanPattern], files: List[Tuple[str, str]],
) -> Tuple[List[FlowEntrypoint], List[str]]:
    """Apply validated regexes to the snapshot and extract API entrypoints.

    Matching is bounded line-by-line so worst-case regex backtracking is limited
    to a single capped-length line. Returns extracted entrypoints (deduplicated
    by ``entrypoint_id``) and per-pattern diagnostics.
    """
    by_path = {p: src for p, src in files}
    entrypoints: List[FlowEntrypoint] = []
    diagnostics: List[str] = []
    seen_ids = set()

    for pat in patterns:
        compiled = re.compile(pat.regex)
        targets = [p for p in by_path if fnmatch.fnmatch(p, pat.file_glob)]
        if not targets:
            diagnostics.append(
                f"Pattern for {pat.framework} matched no files "
                f"(glob {pat.file_glob})."
            )
            continue

        matches = 0
        for path in sorted(targets):
            for lineno, line in enumerate(by_path[path].splitlines(), start=1):
                if len(line) > MAX_LINE_LEN:
                    continue
                m = compiled.search(line)
                if not m:
                    continue
                groups = m.groupdict()
                raw_path = groups.get(pat.path_group) if pat.path_group else None
                if not raw_path:
                    continue
                route_path = _normalize_path(raw_path)
                if pat.method_group and groups.get(pat.method_group):
                    method = groups[pat.method_group].strip().upper()
                elif pat.method_constant:
                    method = pat.method_constant
                else:
                    method = "ANY"
                entrypoint_id = f"{method}:{route_path}"
                if entrypoint_id in seen_ids:
                    continue
                seen_ids.add(entrypoint_id)
                operation = f"{method} {route_path}".strip()
                entrypoints.append(FlowEntrypoint(
                    entrypoint_type="http_route",
                    entrypoint_id=entrypoint_id,
                    label=operation,
                    path=path,
                    qualified_name=route_path,
                    line_start=lineno,
                    line_end=lineno,
                    category="api",
                    framework=pat.framework,
                    operation=operation,
                    route_method=method,
                    route_path=route_path,
                    confidence=pat.confidence,
                    source="reasoning_llm",
                    evidence=[EvidenceRef(
                        path=path, start_line=lineno, end_line=lineno,
                        summary=f"{pat.framework} API declaration: {line.strip()[:160]}",
                    )],
                ))
                matches += 1
                if matches >= MAX_MATCHES_PER_PATTERN:
                    break
            if matches >= MAX_MATCHES_PER_PATTERN:
                break
        if len(entrypoints) >= MAX_EXTRACTED_ENTRYPOINTS:
            diagnostics.append(
                f"Extraction capped at {MAX_EXTRACTED_ENTRYPOINTS} entrypoints."
            )
            break

    entrypoints.sort(key=lambda e: (e.route_path or "", e.route_method or ""))
    return entrypoints, diagnostics
