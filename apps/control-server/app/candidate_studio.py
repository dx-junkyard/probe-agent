"""Structured candidate-proposal generation for the AI Candidate Studio (Issue #252).

probe-agent:
  role: Turns a natural-language improvement instruction into a STRUCTURED
    candidate proposal (summary / assumptions / changed_symbols / risks /
    suggested_tests) plus a deterministically spliced, scope-validated unified
    diff against the pinned snapshot's target symbol
  capability: replay-simulation
  element_type: core
  consumers: [control-server]
  operation_kind: external-api
  state_effects: [filesystem, external-api]
  probe_value: Verify LLM/patch/scope/size failures fail the proposal closed
    (no heuristic fallback) and that a successful proposal's patch is a valid,
    git-applicable diff that changes only the target symbol's file.

Two decision kinds, kept explicit (Principle 6):

  * "What should the candidate look like" -- reasoning model only. The model
    returns a versioned STRUCTURED proposal, never free-form code: summary,
    assumptions, changed_symbols, generated_code (a drop-in ``def candidate``
    replacement), risks, suggested_tests. Any LLM configuration/call/parse/
    validation failure fails the proposal outright -- no heuristic fallback
    (reasoning-llm skill). ``LLM_PROVIDER=mock`` is allowed (visibly marked
    ``is_mock``), matching generation.py / replay_draft.py.
  * "How does that become a diff" -- purely deterministic: the same structural
    splice + worktree ``git diff`` mechanism ``replay_draft`` uses (reused
    here verbatim), then a deterministic patch-scope check enforcing the
    default "only the target symbol's file may change" rule.

Nothing here executes the candidate; execution happens later when the caller
replays the returned patch as a variant via POST /replay-variant-runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from .experiment_runner import patch_hash
from .git_ops import GitError, read_file_at_commit
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from .replay_draft import DraftError, _diff_against_snapshot, splice_symbol_source

PROMPT_VERSION = "candidate-studio-proposal-v1"
SCHEMA_VERSION = "candidate-studio-proposal-v1"

PROPOSAL_PROPOSED = "proposed"
PROPOSAL_FAILED = "failed"

# Deterministic guard rails (Principle 6). generated_code and the resulting
# diff are hard-capped; example traces shown to the model are bounded so the
# prompt context stays the fixed-snapshot target plus minimal neighbours.
MAX_CODE_CHARS = 50_000
MAX_PATCH_CHARS = 200_000
MAX_BASELINE_SOURCE_CHARS = 60_000
MAX_EXAMPLE_TRACES = 5
MAX_LIST_ITEMS = 20


@dataclass
class ProposalResult:
    provider: str
    model: str
    is_mock: bool
    status: str  # 'proposed' | 'failed'
    summary: str = ""
    assumptions: List[str] = field(default_factory=list)
    changed_symbols: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    suggested_tests: List[str] = field(default_factory=list)
    generated_code: str = ""
    patch_text: str = ""
    patch_hash: str = ""
    error: Optional[str] = None


def _string_list(value: Any) -> List[str]:
    """Coerce a proposal list field to a bounded list of non-empty strings."""
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for entry in value:
        if isinstance(entry, str):
            # Blank / whitespace-only strings are dropped, not coerced.
            if entry.strip():
                items.append(entry.strip())
        elif entry is not None and not isinstance(entry, (list, dict)):
            items.append(str(entry))
        if len(items) >= MAX_LIST_ITEMS:
            break
    return items


def build_proposal_prompt(
    context: Dict[str, Any], instruction: str
) -> List[Dict[str, str]]:
    """Prompt for a structured candidate proposal grounded in the pinned
    baseline source. The distinctive ``CANDIDATE_STUDIO_PROPOSAL_JSON`` marker
    lets ``MockLLMClient`` return a deterministic structured proposal for
    local/test smoke runs."""
    payload = {
        "instruction": instruction,
        "objective": context.get("objective", ""),
        "component_id": context.get("component_id", ""),
        "target_symbol": context.get("target_symbol", {}),
        "baseline_source": context.get("baseline_source", ""),
        "system_profile": context.get("system_profile", {}),
        "component_profile": context.get("component_profile", {}),
        "evaluation_criteria": context.get("criteria", []),
        "example_traces": context.get("example_traces", []),
    }
    return [
        {
            "role": "system",
            "content": (
                "You improve an existing Python function for probe-agent. You "
                "are given the function's EXACT current source at a pinned "
                "commit, its component/system profile, evaluation criteria, and "
                "recorded example traces. Propose a drop-in replacement. Return "
                "ONLY JSON. The generated_code MUST define a single top-level "
                "`def candidate(*args, **kwargs)` that preserves the original "
                "call contract. Do not use imports, file I/O, network calls, "
                "subprocesses, or environment access. Change only the target "
                "function's behavior."
            ),
        },
        {
            "role": "user",
            "content": (
                "Produce a candidate proposal for the instruction below.\n"
                "CANDIDATE_STUDIO_PROPOSAL_JSON schema: {"
                "\"summary\": string, \"assumptions\": string[], "
                "\"changed_symbols\": string[], \"generated_code\": string, "
                "\"risks\": string[], \"suggested_tests\": string[]}.\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            ),
        },
    ]


def _validate_patch_scope(patch_text: str, symbol_path: str) -> None:
    """Deterministically enforce the default scope: the diff may touch exactly
    the target symbol's file and no other (Principle 6 / Issue #252 "対象
    シンボルのファイルのみ変更可"). Raises :class:`DraftError` otherwise."""
    touched: List[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            # "diff --git a/<path> b/<path>"
            parts = line.split(" b/", 1)
            if len(parts) == 2 and parts[1] not in touched:
                touched.append(parts[1])
    if not touched:
        raise DraftError("candidate patch touched no files")
    if touched != [symbol_path]:
        raise DraftError(
            "candidate patch changed files outside the target symbol's file "
            f"({symbol_path}): {touched}. Widening scope requires explicit "
            "confirmation and is not allowed by default."
        )


def generate_candidate_proposal(
    client: LLMClient,
    config: LLMConfig,
    *,
    repo_path: str,
    commit_sha: str,
    symbol_path: str,
    symbol_qualified_name: str,
    start_line: int,
    end_line: int,
    context: Dict[str, Any],
    instruction: str,
    worktree_base: str,
) -> ProposalResult:
    """Generate one structured candidate proposal.

    Reasoning model -> structured JSON -> deterministic splice into the
    resolved symbol span -> ``git diff`` against the pinned snapshot ->
    deterministic scope/size validation. Never executes the candidate. Any
    failure returns a ``status='failed'`` result (never raised past the
    caller) so the attempt is still auditable (Principle 7)."""
    is_mock = isinstance(client, MockLLMClient)
    provider = "mock" if is_mock else config.provider
    model = "mock" if is_mock else config.model

    def _fail(error: str) -> ProposalResult:
        return ProposalResult(
            provider=provider, model=model, is_mock=is_mock,
            status=PROPOSAL_FAILED, error=error,
        )

    try:
        raw = client.generate_text(
            build_proposal_prompt(context, instruction),
            temperature=0.2,
            max_tokens=2200,
        )
    except LLMError as exc:
        return _fail(str(exc))

    # Local import avoids a route<-service import cycle; reuses the exact
    # JSON extraction POST /generation-runs already uses.
    from .routes.generation import _extract_json_object

    try:
        proposal = _extract_json_object(raw)
    except HTTPException as exc:
        return _fail(str(exc.detail))
    except ValueError as exc:  # includes json.JSONDecodeError
        return _fail(f"Failed to parse LLM response: {exc}")

    generated_code = str(proposal.get("generated_code") or "").strip()
    if "def candidate" not in generated_code:
        return _fail("LLM response did not define a candidate function")
    if len(generated_code) > MAX_CODE_CHARS:
        return _fail(
            f"generated candidate exceeds {MAX_CODE_CHARS} characters"
        )

    summary = str(proposal.get("summary") or "")
    assumptions = _string_list(proposal.get("assumptions"))
    changed_symbols = _string_list(proposal.get("changed_symbols"))
    risks = _string_list(proposal.get("risks"))
    suggested_tests = _string_list(proposal.get("suggested_tests"))

    try:
        original_bytes = read_file_at_commit(repo_path, commit_sha, symbol_path)
    except GitError as exc:
        return _fail(f"Could not read {symbol_path} at {commit_sha}: {exc}")
    try:
        original_source = original_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _fail(f"{symbol_path} is not valid UTF-8: {exc}")

    simple_name = symbol_qualified_name.split(".")[-1]
    try:
        new_source = splice_symbol_source(
            original_source, start_line, end_line, generated_code, simple_name
        )
    except DraftError as exc:
        return _fail(str(exc))

    try:
        patch_text = _diff_against_snapshot(
            repo_path, commit_sha, symbol_path, new_source, worktree_base
        )
    except (DraftError, GitError) as exc:
        return _fail(str(exc))

    if not patch_text.strip():
        return _fail(
            "generated candidate produced no diff against the pinned snapshot"
        )
    if len(patch_text) > MAX_PATCH_CHARS:
        return _fail(f"candidate patch exceeds {MAX_PATCH_CHARS} characters")
    try:
        _validate_patch_scope(patch_text, symbol_path)
    except DraftError as exc:
        return _fail(str(exc))

    return ProposalResult(
        provider=provider,
        model=model,
        is_mock=is_mock,
        status=PROPOSAL_PROPOSED,
        summary=summary,
        assumptions=assumptions,
        changed_symbols=changed_symbols,
        risks=risks,
        suggested_tests=suggested_tests,
        generated_code=generated_code,
        patch_text=patch_text,
        patch_hash=patch_hash(patch_text),
    )
