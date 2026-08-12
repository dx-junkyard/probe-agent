"""Read-only candidate discovery beyond the file/symbol indexes (Issue #339).

Epic #328's investigation loop retrieved candidates from four sources: file
paths, the symbol index, the entrypoint index, and a content scan. All four
answer the same question -- "which file MENTIONS this?" -- so a question whose
answer lives in the *structure* around a file could not be reached. Three
things the developer routinely needs were therefore unreachable:

- **who depends on this** (a change here affects those callers),
- **who calls this** (the behaviour is decided upstream, not here),
- **when and why this changed** (the reason is in the history, not the code).

This module adds those three as explicit sources, plus runtime facts used for
DISCOVERY rather than only as a supplement to files already chosen. Every one
of them is deterministic candidate RETRIEVAL: it narrows which files the
reasoning model may read and never decides anything (Principle 6). Ordering is
total and stable, so the same revision and the same seeds always produce the
same candidate list.

The read-only boundary (Principle 5) is enforced per source, not assumed:

- the index/content sources read rows captured FOR the pinned snapshot,
- the git history source runs read-only plumbing with the PINNED commit as the
  tip. It can never see a commit that is not an ancestor of it, and never the
  working tree -- so a source that would let a newer change leak into a pinned
  investigation does not exist here.

Every source reports its own ``revision``, its own budget consumption, and its
own failure. A source that fails is recorded and skipped; it never fails the
round, and it never silently contributes an unbounded fallback search
(Issue #339: 予算超過時に無制限の fallback 探索を行わない).

probe-agent:
  role: deterministic read-only candidate discovery from dependencies, call
    sites, git history, and runtime facts against a pinned revision
  capability: interactive-system-understanding
  element_type: core
  operation_kind: io
  state_effects: [database-read]
  probe_value: Verify every source reads only the pinned revision (never the working tree, never a commit outside the pinned commit's history), that each reports its own revision/budget/failure, and that a failing source is skipped rather than escalated or replaced by an unbounded search.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from .git_ops import GitError, _run_git

# Finite source kinds (Principle 6). The first four are Epic #328's; the last
# four are Issue #339's additions. An unknown value is rejected, never treated
# as a generic source.
EXPLORATION_SOURCE_KINDS = (
    "path_name",
    "symbol_index",
    "entrypoint_index",
    "file_content",
    "dependency",
    "call_graph",
    "git_history",
    "runtime_facts",
)

# Deterministic caps. Each source has its own so one cannot starve the others,
# and none of them is a soft hint: a source that hits its cap stops there and
# reports what it consumed.
MAX_DEPENDENCY_CANDIDATES = 20
MAX_CALL_GRAPH_CANDIDATES = 20
MAX_GIT_HISTORY_COMMITS = 20
MAX_GIT_HISTORY_CANDIDATES = 20
MAX_RUNTIME_DISCOVERY_CANDIDATES = 10
MAX_SEEDS = 8

GIT_HISTORY_TIMEOUT_SECONDS = 20


@dataclass
class ExplorerResult:
    """One source's contribution to one round, with its own audit facts."""

    source_kind: str
    # What the source actually read: the pinned commit for git history, the
    # snapshot id (as a string) for the index/content/runtime sources. Recorded
    # per source because "the round used the pinned revision" is a claim that
    # has to be checkable for each source separately.
    revision: str
    candidates: List[str] = field(default_factory=list)
    # Deterministic cost, for the per-source budget audit.
    queries_run: int = 0
    elapsed_seconds: float = 0.0
    truncated: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _module_names(path: str) -> List[str]:
    """The import names a Python path could be referenced by, longest first.

    ``apps/control-server/app/retry.py`` yields ``app.retry``, ``retry`` and so
    on: enough to match a real import line without needing the project's import
    roots, which are not knowable from a snapshot alone.
    """
    if not path.endswith(".py"):
        return []
    parts = [p for p in path[: -len(".py")].split("/") if p and p != "__init__"]
    if not parts:
        return []
    names: List[str] = []
    for start in range(len(parts)):
        names.append(".".join(parts[start:]))
    # Longest (most specific) first so a match is attributed to the most
    # precise name rather than to a bare leaf that collides across packages.
    return sorted(set(names), key=lambda n: (-len(n), n))


def dependency_candidates(
    conn,
    *,
    snapshot_id: int,
    system_id: int,
    seed_paths: Sequence[str],
    limit: int = MAX_DEPENDENCY_CANDIDATES,
) -> ExplorerResult:
    """Files that import a seed path, and files a seed path imports.

    Both directions matter and they answer different questions: the importers
    are who a change here would affect, and the imports are where this file's
    behaviour actually comes from.

    The source is ``code_symbols.imports`` -- the AST-extracted import list the
    Issue #24 indexer already captured for the pinned snapshot, stored as
    ``"module:name,name"`` strings. Using the real index rather than a text
    scan is what makes this a structural relation instead of a mention: a
    docstring naming a module is not a dependency on it.
    """
    result = ExplorerResult(source_kind="dependency", revision=str(snapshot_id))
    seeds = [p for p in dict.fromkeys(seed_paths) if p][:MAX_SEEDS]
    if not seeds:
        return result

    hits: Dict[str, int] = {}

    # Reverse: who imports a module one of the seeds defines.
    patterns: List[str] = []
    for seed in seeds:
        for name in _module_names(seed)[:3]:
            patterns.append(f'%"{name}:%')
            patterns.append(f'%"{name}"%')
    if patterns:
        clause = " OR ".join(["imports LIKE ?"] * len(patterns))
        rows = conn.execute(
            f"""SELECT DISTINCT path FROM code_symbols
                WHERE snapshot_id = ? AND system_id = ? AND ({clause})
                ORDER BY path
                LIMIT ?""",
            (snapshot_id, system_id, *patterns, limit * 3),
        ).fetchall()
        result.queries_run += 1
        for row in rows:
            if row["path"] not in seeds:
                hits[row["path"]] = hits.get(row["path"], 0) + 2

    # Forward: what the seeds themselves import.
    seed_rows = conn.execute(
        f"""SELECT imports FROM code_symbols
            WHERE snapshot_id = ? AND system_id = ?
              AND path IN ({','.join('?' for _ in seeds)})
            ORDER BY path""",
        (snapshot_id, system_id, *seeds),
    ).fetchall()
    result.queries_run += 1
    imported_modules: Set[str] = set()
    for row in seed_rows:
        try:
            entries = json.loads(row["imports"] or "[]")
        except (TypeError, ValueError):
            continue
        for entry in entries:
            module = str(entry).split(":", 1)[0].strip()
            # A relative import ("" or leading dots) names no module we can
            # resolve from the snapshot alone; the leaf is what a path can match.
            leaf = module.lstrip(".").split(".")[-1]
            if len(leaf) > 2:
                imported_modules.add(leaf)
    if imported_modules:
        like = [f"%/{name}.py" for name in sorted(imported_modules)[: MAX_SEEDS * 2]]
        clause = " OR ".join(["path LIKE ?"] * len(like))
        rows = conn.execute(
            f"""SELECT DISTINCT path FROM code_symbols
                WHERE snapshot_id = ? AND system_id = ? AND ({clause})
                ORDER BY path
                LIMIT ?""",
            (snapshot_id, system_id, *like, limit * 2),
        ).fetchall()
        result.queries_run += 1
        for row in rows:
            if row["path"] not in seeds:
                hits[row["path"]] = hits.get(row["path"], 0) + 1

    ordered = sorted(hits.items(), key=lambda item: (-item[1], item[0]))
    result.truncated = len(ordered) > limit
    result.candidates = [path for path, _ in ordered[:limit]]
    return result


def call_graph_candidates(
    conn,
    *,
    snapshot_id: int,
    system_id: int,
    seed_paths: Sequence[str],
    seed_symbols: Sequence[str] = (),
    limit: int = MAX_CALL_GRAPH_CANDIDATES,
) -> ExplorerResult:
    """Files containing a call site of a symbol defined in the seeds.

    The question this answers is the one a mention-based scan cannot: where the
    behaviour is actually invoked from. Call sites are matched structurally on
    ``name(``, which is a shape, not a keyword score.
    """
    result = ExplorerResult(source_kind="call_graph", revision=str(snapshot_id))
    seeds = [p for p in dict.fromkeys(seed_paths) if p][:MAX_SEEDS]
    names: List[str] = [s for s in dict.fromkeys(seed_symbols) if s][:MAX_SEEDS]
    if seeds:
        rows = conn.execute(
            f"""SELECT qualified_name FROM code_symbols
                WHERE snapshot_id = ? AND system_id = ?
                  AND path IN ({','.join('?' for _ in seeds)})
                  AND kind IN ('function', 'async_function', 'class')
                ORDER BY path, start_line
                LIMIT ?""",
            (snapshot_id, system_id, *seeds, MAX_SEEDS * 4),
        ).fetchall()
        result.queries_run += 1
        for row in rows:
            simple = (row["qualified_name"] or "").split(".")[-1]
            # A one- or two-character name would match almost anything; a
            # dunder is defined everywhere. Neither narrows the search.
            if len(simple) > 3 and not simple.startswith("__"):
                names.append(simple)
    names = list(dict.fromkeys(names))[: MAX_SEEDS * 2]
    if not names:
        return result

    patterns = [f"%{name}(%" for name in names]
    clause = " OR ".join(["CAST(f.content AS TEXT) LIKE ?"] * len(patterns))
    rows = conn.execute(
        f"""SELECT f.path AS path FROM snapshot_files AS f
            WHERE f.snapshot_id = ? AND f.inclusion_status = 'indexed' AND ({clause})
            ORDER BY f.path
            LIMIT ?""",
        (snapshot_id, *patterns, limit * 3),
    ).fetchall()
    result.queries_run += 1
    ordered = [row["path"] for row in rows if row["path"] not in seeds]
    result.truncated = len(ordered) > limit
    result.candidates = ordered[:limit]
    return result


def git_history_candidates(
    *,
    repo_path: str,
    commit_sha: str,
    seed_paths: Sequence[str],
    max_commits: int = MAX_GIT_HISTORY_COMMITS,
    limit: int = MAX_GIT_HISTORY_CANDIDATES,
    timeout_seconds: float = GIT_HISTORY_TIMEOUT_SECONDS,
) -> ExplorerResult:
    """Files that changed together with the seeds, in the pinned history.

    ``commit_sha`` is the tip, so ``git log`` can only walk its ancestors:
    a commit made after the snapshot was pinned is not reachable, and the
    working tree is never consulted. That is what keeps the added source inside
    Principle 5's boundary rather than beside it.

    Co-change is retrieval, not causation: a file that keeps changing alongside
    the seed is worth READING, and what (if anything) it explains is the
    reasoning model's call on the evidence.
    """
    result = ExplorerResult(source_kind="git_history", revision=commit_sha)
    started = time.monotonic()
    seeds = [p for p in dict.fromkeys(seed_paths) if p][:MAX_SEEDS]
    if not seeds:
        return result
    # Two steps, deliberately. `git log --name-only -- <pathspec>` filters the
    # shown file list to the pathspec as well, so a single call can only ever
    # report the seed back -- the co-changed files, which are the whole point,
    # are filtered out. So: find the commits that touched the seeds, then list
    # what those commits changed.
    try:
        log = _run_git(
            repo_path,
            ["log", commit_sha, f"-n{max_commits}", "--format=%H", "--", *seeds],
            timeout=max(0.001, min(GIT_HISTORY_TIMEOUT_SECONDS, timeout_seconds)),
        )
    except GitError as exc:
        result.error = str(exc)
        return result
    result.queries_run += 1
    result.elapsed_seconds = time.monotonic() - started
    if log.returncode != 0:
        result.error = (
            log.stderr.decode("utf-8", errors="replace").strip()
            or "git log failed for the pinned commit"
        )
        return result
    commits = [
        line.strip()
        for line in log.stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ][:max_commits]
    if not commits:
        return result

    remaining_seconds = timeout_seconds - (time.monotonic() - started)
    if remaining_seconds <= 0:
        result.error = "git history exploration exceeded its time budget"
        return result
    try:
        # --no-walk lists exactly these commits and nothing else, so the walk
        # cannot leave the pinned commit's history even though no pathspec
        # constrains it here.
        log = _run_git(
            repo_path,
            ["log", "--no-walk", "--format=%x00%H", "--name-only", *commits],
            timeout=max(0.001, min(GIT_HISTORY_TIMEOUT_SECONDS, remaining_seconds)),
        )
    except GitError as exc:
        result.error = str(exc)
        return result
    result.queries_run += 1
    result.elapsed_seconds = time.monotonic() - started
    if log.returncode != 0:
        result.error = (
            log.stderr.decode("utf-8", errors="replace").strip()
            or "git log failed for the pinned commits"
        )
        return result

    hits: Dict[str, int] = {}
    for line in log.stdout.decode("utf-8", errors="replace").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("\x00"):
            continue
        if entry in seeds:
            continue
        hits[entry] = hits.get(entry, 0) + 1
    ordered = sorted(hits.items(), key=lambda item: (-item[1], item[0]))
    result.truncated = len(ordered) > limit
    result.candidates = [path for path, _ in ordered[:limit]]
    return result


def runtime_discovery_candidates(
    conn,
    *,
    snapshot_id: int,
    system_id: int,
    limit: int = MAX_RUNTIME_DISCOVERY_CANDIDATES,
) -> ExplorerResult:
    """Paths of components the system has actually been observed executing.

    Epic #328 used runtime facts only to annotate files it had ALREADY chosen
    to read, which means a component that is failing in production could not
    bring its own source into the candidate set. Ordering is by observed error
    count then call count then component id: what is going wrong is worth
    reading first, and the tie-breaks keep the list reproducible.
    """
    result = ExplorerResult(source_kind="runtime_facts", revision=str(snapshot_id))
    if limit <= 0:
        return result
    # DISTINCT on the trace identity, not COUNT(*): one path can define several
    # symbols that share a component_id, and the join then yields one row per
    # (trace, symbol) pair. Counting those would weight a path by how many
    # symbols it happens to contain, letting a large file jump the queue ahead of
    # one that is actually failing more often -- while still looking like a
    # count of calls.
    rows = conn.execute(
        """SELECT s.path AS path,
                  COUNT(DISTINCT CASE
                      WHEN t.error IS NOT NULL AND t.error != '' THEN t.trace_id
                  END) AS errors,
                  COUNT(DISTINCT t.trace_id) AS calls
           FROM traces AS t
           JOIN code_symbols AS s
             ON s.component_id = t.component_id AND s.snapshot_id = ?
           WHERE t.system_id = ?
           GROUP BY s.path
           ORDER BY errors DESC, calls DESC, s.path
           LIMIT ?""",
        (snapshot_id, system_id, limit),
    ).fetchall()
    result.queries_run += 1
    result.candidates = [row["path"] for row in rows]
    return result


def merge_candidates(
    results: Sequence[ExplorerResult],
    *,
    tracked_paths: Set[str],
    already_read: Set[str],
    limit: int,
) -> List[str]:
    """Interleave sources' candidates, keeping each source represented.

    Round-robin rather than concatenation, deliberately: concatenating means a
    single prolific source consumes the whole read budget and the sources added
    for breadth contribute nothing. The order within each source is already
    deterministic, so this is too.

    ``tracked_paths`` is the pinned snapshot's file list -- a candidate that is
    not in it cannot be read from the pinned commit, so it is dropped here
    rather than failing later.
    """
    queues = [list(r.candidates) for r in results if r.ok]
    ordered: List[str] = []
    seen: Set[str] = set()
    index = 0
    while queues and len(ordered) < limit:
        progressed = False
        for queue in queues:
            if index >= len(queue):
                continue
            progressed = True
            path = queue[index]
            if path in seen or path in already_read or path not in tracked_paths:
                continue
            seen.add(path)
            ordered.append(path)
            if len(ordered) >= limit:
                break
        if not progressed:
            break
        index += 1
    return ordered


__all__ = [
    "EXPLORATION_SOURCE_KINDS",
    "GIT_HISTORY_TIMEOUT_SECONDS",
    "MAX_CALL_GRAPH_CANDIDATES",
    "MAX_DEPENDENCY_CANDIDATES",
    "MAX_GIT_HISTORY_CANDIDATES",
    "MAX_GIT_HISTORY_COMMITS",
    "MAX_RUNTIME_DISCOVERY_CANDIDATES",
    "ExplorerResult",
    "call_graph_candidates",
    "dependency_candidates",
    "git_history_candidates",
    "merge_candidates",
    "runtime_discovery_candidates",
]
