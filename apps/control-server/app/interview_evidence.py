"""Deterministic evidence fetch for the interview dialogue turn (Issue #130).

Pass 1 of the dialogue turn (a reasoning-model call in ``interview_agent.py``)
selects up to N ``(path, start_line, end_line)`` targets to read before asking
a question, or declares that no evidence is needed. This module handles the
deterministic half of that split (Principle 6):

- Validating that each target's ``path`` is one the model is allowed to
  reference and that its line range is contained within a span surfaced for
  that path (it must appear in the context pack or current understanding —
  the same allowed-span set ``interview_agent._allowed_evidence_spans`` uses
  for question evidence_refs, enforced with the same containment check).
- Reading the validated targets from the *pinned* commit via
  ``git_ops.read_file_at_commit`` (never the working tree, never untracked
  content — Principle 5), enforcing a per-file line cap and a total
  character budget so a pathological selection cannot blow up the prompt.

Any read failure (missing path, git error) fails the whole turn closed; the
budget caps truncate rather than fail, since they bound cost, not safety.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .git_ops import GitError, read_file_at_commit

DEFAULT_MAX_FILES = 5
DEFAULT_MAX_CHARS = 20_000
DEFAULT_MAX_LINES_PER_FILE = 200


class EvidenceConfigError(ValueError):
    """Raised when an INTERVIEW_EVIDENCE_* env var is not a valid positive int."""


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise EvidenceConfigError(f"{name} must be an integer") from exc
    if value < 1:
        raise EvidenceConfigError(f"{name} must be positive")
    return value


def max_files() -> int:
    return _positive_int_env("INTERVIEW_EVIDENCE_MAX_FILES", DEFAULT_MAX_FILES)


def max_chars() -> int:
    return _positive_int_env("INTERVIEW_EVIDENCE_MAX_CHARS", DEFAULT_MAX_CHARS)


def max_lines_per_file() -> int:
    return _positive_int_env(
        "INTERVIEW_EVIDENCE_MAX_LINES_PER_FILE", DEFAULT_MAX_LINES_PER_FILE
    )


@dataclass
class EvidenceTarget:
    path: str
    start_line: int
    end_line: int
    reason: str = ""


@dataclass
class EvidenceSnippet:
    path: str
    start_line: int
    end_line: int
    content: str
    char_count: int
    truncated: bool = False


class EvidenceReadError(Exception):
    """Fail-closed error: the turn must not continue without the snippet.

    ``partial_snippets`` carries whatever snippets were successfully read
    before the failing target, so the caller can still persist an audit of
    what was actually read (Issue #137) even though the turn fails closed.
    """

    def __init__(self, message: str, partial_snippets: Optional[List["EvidenceSnippet"]] = None):
        super().__init__(message)
        self.partial_snippets: List["EvidenceSnippet"] = partial_snippets or []


def validate_evidence_targets(
    targets: List[EvidenceTarget],
    allowed_paths: Dict[str, List[Tuple[int, int]]],
    *,
    limit: Optional[int] = None,
) -> Optional[str]:
    """Structural validation of pass-1 targets. Returns an error string or None.

    ``allowed_paths`` is the same context-pack/current-understanding path set
    used to validate question evidence_refs — evidence targets are limited to
    paths and symbols already surfaced there, never invented.
    """
    file_limit = limit if limit is not None else max_files()
    if len(targets) > file_limit:
        return (
            f"requested {len(targets)} evidence targets, exceeding the "
            f"INTERVIEW_EVIDENCE_MAX_FILES limit of {file_limit}"
        )
    for target in targets:
        if target.path not in allowed_paths:
            return (
                f"evidence target references unknown path '{target.path}' "
                "(not in context pack or current understanding)"
            )
        if target.start_line < 1 or target.end_line < target.start_line:
            return (
                f"evidence target has invalid line range "
                f"{target.start_line}-{target.end_line} for '{target.path}'"
            )
        if not any(
            _span_contains(target, span) for span in allowed_paths[target.path]
        ):
            return (
                f"evidence target lines {target.start_line}-{target.end_line} are "
                f"not contained in any known span in '{target.path}'"
            )
    return None


def _span_contains(target: EvidenceTarget, span: Tuple[int, int]) -> bool:
    """Whether ``target``'s line range is contained within ``span``.

    Mirrors ``interview_agent._span_matches`` so pass-1 evidence targets are
    bounded to the exact snapshot spans surfaced in the context pack, not just
    a known path — a target requesting rows outside every known span is
    fail-closed, matching the question-side evidence_ref check (Issue #130).
    """
    start, end = span
    if start < 1 or end < start:
        # No real line range is known for this path; nothing can be
        # contained within it.
        return False
    return target.start_line >= start and target.end_line <= end


def read_evidence_snippets(
    repo_path: str,
    commit_sha: str,
    targets: List[EvidenceTarget],
) -> List[EvidenceSnippet]:
    """Read validated targets from the pinned commit, budget-bounded.

    Raises ``EvidenceReadError`` if any target cannot be read — there is no
    "continue without this snippet" fallback (Issue #130 non-goal).
    """
    per_file_cap = max_lines_per_file()
    total_budget = max_chars()

    snippets: List[EvidenceSnippet] = []
    remaining_budget = total_budget
    for target in targets:
        try:
            raw = read_file_at_commit(repo_path, commit_sha, target.path)
        except GitError as exc:
            raise EvidenceReadError(
                f"failed to read evidence '{target.path}' at {commit_sha}: {exc}",
                partial_snippets=snippets,
            ) from exc

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(0, target.start_line - 1)
        end = min(len(lines), target.end_line)
        span_lines = lines[start:end]

        line_truncated = len(span_lines) > per_file_cap
        if line_truncated:
            span_lines = span_lines[:per_file_cap]

        content = "\n".join(span_lines)
        char_truncated = False
        if remaining_budget <= 0:
            content = ""
            char_truncated = True
        elif len(content) > remaining_budget:
            content = content[:remaining_budget]
            char_truncated = True

        remaining_budget -= len(content)

        snippets.append(EvidenceSnippet(
            path=target.path,
            start_line=target.start_line,
            end_line=min(target.end_line, start + len(span_lines)),
            content=content,
            char_count=len(content),
            truncated=line_truncated or char_truncated,
        ))

    return snippets
