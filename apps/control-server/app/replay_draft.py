"""LLM candidate-draft generation for Replay variants (Issue #242 Phase C / #245).

probe-agent:
  role: Connects the existing Generate & Evaluate flow as a "candidate draft"
    source for Replay variants -- reasoning-model code generation, then a
    deterministic structural line-splice into a reviewable unified diff
  capability: replay-simulation
  element_type: core
  consumers: [control-server]
  operation_kind: external-api
  state_effects: [filesystem, external-api]
  probe_value: Verify LLM failures fail the draft operation closed (no
    heuristic fallback) and that a successful draft's patch is a valid,
    git-applicable diff against the pinned snapshot.

Two decision kinds, kept explicit (Principle 6):

  * "What should the candidate code look like" -- reasoning model only
    (``generate_variant_draft`` calls the SAME candidate-generation prompt
    ``app/routes/generation.py`` already uses for ``POST /generation-runs``:
    same system/user prompt builder, same JSON response contract, same
    ``"def candidate"`` requirement). Any LLM configuration/call/parse
    failure fails the draft outright -- no heuristic fallback, matching the
    ``reasoning-llm`` skill. ``LLM_PROVIDER=mock`` is explicitly allowed here
    (unlike Issue #24's code-mapper, which requires a real reasoning model):
    generation.py has always treated the mock provider as a legitimate
    deterministic stand-in for local/test use, and Issue #245 requires
    ``is_mock=true`` to be visibly surfaced, never a rejection.
  * "How does that code become a diff" -- purely deterministic, structural
    text splicing (``splice_symbol_source``): the resolved symbol's exact
    ``[start_line, end_line]`` span (from ``code_symbols``, the same
    deterministic AST index Issue #24 built) is replaced by the generated
    function body, renamed to the symbol's own simple name and reindented to
    match the original ``def`` line -- no inference, just text substitution
    plus an ``ast.parse`` structural validity check on both the standalone
    function and the final spliced file. The diff itself is produced by
    writing that file into a real temporary worktree and running ``git
    diff`` (the same worktree/diff mechanism ``patch_generator.generate_patch``
    already uses for Issue #25 patches), so the result is byte-for-byte the
    same kind of unified diff ``git apply`` / ``_apply_patch`` already
    consume elsewhere in this codebase -- no hand-rolled diff format.

Nothing here executes the candidate; execution only happens later, when the
caller runs the returned patch as a variant via ``POST /replay-variant-runs``.
"""

from __future__ import annotations

import ast
import os
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException

from .experiment_runner import patch_hash
from .git_ops import GitError, _run_git, read_file_at_commit
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from .patch_generator import cleanup_worktree, create_worktree

PROMPT_VERSION = "replay-variant-draft-v1"
SCHEMA_VERSION = "replay-variant-draft-v1"

DRAFT_PROPOSED = "proposed"
DRAFT_FAILED = "failed"


@dataclass
class DraftResult:
    provider: str
    model: str
    is_mock: bool
    status: str  # 'proposed' | 'failed'
    generated_code: str = ""
    notes: str = ""
    patch_text: str = ""
    patch_hash: str = ""
    error: Optional[str] = None


class DraftError(ValueError):
    """A deterministic, structural failure while turning generated code into
    a diff (line range out of bounds, no 'def candidate', invalid Python,
    empty diff). Always represented as a failed DraftResult, never raised
    past the caller."""


def _rename_candidate(generated_code: str, simple_name: str) -> str:
    """Structural rename of ``def candidate(`` to the resolved symbol's own
    simple name (the last dotted segment of ``qualified_name``). Pure text
    substitution -- the function's signature/body is untouched."""
    match = re.search(r"(?m)^def\s+candidate\s*\(", generated_code)
    if not match:
        raise DraftError(
            "generated code must define a top-level 'def candidate(' function"
        )
    return generated_code[: match.start()] + f"def {simple_name}(" + generated_code[match.end():]


def splice_symbol_source(
    original_source: str,
    start_line: int,
    end_line: int,
    generated_code: str,
    simple_name: str,
) -> str:
    """Deterministically replace lines ``[start_line, end_line]`` (1-based,
    inclusive -- the exact ``code_symbols.start_line``/``end_line`` span,
    which starts at the ``def`` line and excludes any decorator lines above
    it) with the LLM-generated function, renamed and reindented to match.

    Raises :class:`DraftError` for any structural problem (out-of-range
    lines, missing ``def candidate(``, or a result that fails to parse as
    valid Python either standalone or spliced into the file) -- never
    silently degrades.
    """
    lines = original_source.splitlines(keepends=True)
    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        raise DraftError(
            f"symbol line range {start_line}-{end_line} is out of bounds for "
            f"a {len(lines)}-line file"
        )
    def_line = lines[start_line - 1]
    indent = def_line[: len(def_line) - len(def_line.lstrip())]

    renamed = _rename_candidate(generated_code, simple_name)
    if not renamed.endswith("\n"):
        renamed += "\n"
    try:
        ast.parse(renamed)
    except SyntaxError as exc:
        raise DraftError(f"generated candidate is not valid Python: {exc}") from exc

    indented = textwrap.indent(renamed, indent)
    new_source = "".join(lines[: start_line - 1] + [indented] + lines[end_line:])
    try:
        ast.parse(new_source)
    except SyntaxError as exc:
        raise DraftError(f"spliced source is not valid Python: {exc}") from exc
    return new_source


def _diff_against_snapshot(
    repo_path: str,
    commit_sha: str,
    symbol_path: str,
    new_source: str,
    worktree_base: str,
) -> str:
    """Materialize ``new_source`` at ``symbol_path`` inside a fresh temporary
    worktree pinned to ``commit_sha`` and return ``git diff`` against it.
    Always cleans the worktree up. This is the exact mechanism
    ``patch_generator.generate_patch`` uses for Issue #25 patches, reused so
    the resulting diff is a normal ``git apply``-compatible unified diff."""
    worktree = create_worktree(repo_path, commit_sha, worktree_base)
    try:
        full_path = os.path.join(worktree, symbol_path)
        normalized = os.path.realpath(full_path)
        worktree_real = os.path.realpath(worktree)
        if os.path.islink(full_path) or not normalized.startswith(worktree_real + os.sep):
            raise DraftError(f"{symbol_path}: path traversal or symlink detected")
        if not os.path.isfile(full_path):
            raise DraftError(f"{symbol_path}: file not found in worktree")
        with open(full_path, "w", encoding="utf-8") as handle:
            handle.write(new_source)
        diff_result = _run_git(worktree, ["diff"], timeout=30)
        if diff_result.returncode != 0:
            stderr = diff_result.stderr.decode("utf-8", errors="replace").strip()
            raise DraftError(f"git diff failed while building the candidate patch: {stderr}")
        return diff_result.stdout.decode("utf-8", errors="replace")
    finally:
        cleanup_worktree(repo_path, worktree)


def generate_variant_draft(
    client: LLMClient,
    config: LLMConfig,
    *,
    repo_path: str,
    commit_sha: str,
    symbol_path: str,
    symbol_qualified_name: str,
    start_line: int,
    end_line: int,
    trace_context: Dict[str, Any],
    objective: str,
    worktree_base: str,
) -> DraftResult:
    """Generate a candidate variant patch for one trace.

    Reuses ``app.routes.generation``'s candidate-generation prompt builder
    and JSON-extraction helper verbatim (same reasoning-model contract as
    ``POST /generation-runs``), then deterministically turns the resulting
    ``generated_code`` into a unified diff against the pinned snapshot's
    ``symbol_path`` source. Does not execute anything; the caller runs the
    returned ``patch_text`` as a variant via ``POST /replay-variant-runs``.
    """
    from .routes.generation import _build_generation_prompt, _extract_json_object

    is_mock = isinstance(client, MockLLMClient)
    provider = "mock" if is_mock else config.provider
    model = "mock" if is_mock else config.model

    try:
        raw = client.generate_text(
            _build_generation_prompt(trace_context, objective),
            temperature=0.2,
            max_tokens=1800,
        )
    except LLMError as exc:
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED, error=str(exc),
        )

    try:
        generation = _extract_json_object(raw)
    except HTTPException as exc:
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED, error=str(exc.detail),
        )
    except ValueError as exc:  # includes json.JSONDecodeError
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED, error=f"Failed to parse LLM response: {exc}",
        )

    generated_code = str(generation.get("generated_code") or "").strip()
    if "def candidate" not in generated_code:
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED,
            error="LLM response did not define a candidate function",
        )
    notes = str(generation.get("notes") or "")

    try:
        original_bytes = read_file_at_commit(repo_path, commit_sha, symbol_path)
    except GitError as exc:
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED,
            error=f"Could not read {symbol_path} at {commit_sha}: {exc}",
        )
    try:
        original_source = original_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED, error=f"{symbol_path} is not valid UTF-8: {exc}",
        )

    simple_name = symbol_qualified_name.split(".")[-1]
    try:
        new_source = splice_symbol_source(
            original_source, start_line, end_line, generated_code, simple_name
        )
    except DraftError as exc:
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED, error=str(exc),
        )

    try:
        patch_text = _diff_against_snapshot(
            repo_path, commit_sha, symbol_path, new_source, worktree_base
        )
    except (DraftError, GitError) as exc:
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED, error=str(exc),
        )

    if not patch_text.strip():
        return DraftResult(
            provider=provider, model=model, is_mock=is_mock,
            status=DRAFT_FAILED,
            error="generated candidate produced no diff against the pinned snapshot",
        )

    return DraftResult(
        provider=provider,
        model=model,
        is_mock=is_mock,
        status=DRAFT_PROPOSED,
        generated_code=generated_code,
        notes=notes,
        patch_text=patch_text,
        patch_hash=patch_hash(patch_text),
    )
