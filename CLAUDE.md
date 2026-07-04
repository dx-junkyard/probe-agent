# probe-agent 開発指示

## Project Overview

`probe-agent` is a runtime probe and evaluation platform for tracing, comparing, and evolving software components.

The MVP focuses on Python functions and supports:

- `@probe(component_id="...")`
- input / output / error / duration tracing
- Control Server trace ingestion
- component-level policy
- `off` / `trace` / `shadow` modes
- shadow comparison between current and candidate implementations
- manual evaluation before adoption
- System-scoped repositories and runtime data
- LLM-backed candidate generation and evaluation

The next phases add a Feature Intelligence Layer and an isolated Experiment
Workspace. See `docs/project-intelligence.md`.

Do not implement unsafe automatic replacement in the MVP.

---

## Architecture

This repository is a monorepo.

```text
apps/
  control-server/     FastAPI server for traces, policies, and comparisons
  dashboard/          Simple dashboard for trace inspection and mode control

packages/
  python-probe/       Python SDK providing @probe

shared/
  schemas/            Shared JSON schemas and data contracts

examples/
  simple-pipeline/    Example app for validating the MVP

docs/
  design.md
  mvp.md
  project-intelligence.md
```

---

## Current Roadmap

Implement Feature Intelligence in dependency order. Do not skip ahead by
creating incomplete persistence or execution paths for later phases.

1. Issue #23 — Repository Understanding MVP
   - committed-files-only snapshot
   - evidence-backed System Profile / Feature Map drafts
2. Issue #24 — Feature-to-Code Mapping MVP
   - deterministic Python AST index
   - reasoning-model mapping from Feature to code symbols
3. Issue #25 — Probe Plan / Temporary Patch MVP
   - reasoning-model probe planning
   - approved instrumentation in an isolated worktree only
4. Issue #26 — Experiment Workspace Runner MVP
   - baseline and source-patch variants
   - deterministic metrics plus reasoning-model interpretation

The existing `GET /project-intelligence` endpoint and the Repository,
Feature Map, Probe Planner, and Experiments tabs are explicit mocks. Replace
each mock only when implementing its owning issue; do not silently present
mock data as persisted or analyzed data.

A conversational system-understanding interview that proposes `probe-agent:`
docstring metadata and probe instrumentation together (see Principle 8) is a
future phase building on #25's isolated-worktree instrumentation. It is not
owned by #23-#26 as written; do not start implementing it until it has its
own issue, so persistence and approval flows are designed deliberately
instead of being bolted onto an unrelated issue's scope.

---

## Core Design Principles

1. Safety first.
   - Default behavior must preserve the original function behavior.
   - If the Control Server is unavailable, the original function must run normally.
   - `replace` mode is out of scope for MVP.
   - `shadow` mode must never affect the returned production value.

2. Probe must be lightweight.
   - Minimize overhead.
   - Avoid blocking the target function whenever possible.
   - Never make tracing failures break the target application.

3. Schemas are contracts.
   - `TraceEvent`, `ControlPolicy`, and `ShadowResult` must remain consistent across SDK, server, dashboard, and examples.
   - Schema changes must update shared schemas, server models, SDK types, tests, and docs together.

4. Start with pure-ish components.
   - The MVP should target functions such as summarize, classify, normalize, extract, retrieve.
   - Avoid payment, email sending, DB writes, irreversible side effects, and authentication logic as shadow targets.

5. Read target repositories from Git, not the working tree.
   - Pin a commit SHA before analysis.
   - Enumerate with `git ls-files` and read with `git show <sha>:<path>`.
   - Never read untracked, ignored, or uncommitted file contents.
   - Reject path traversal and repository-external symlink access.
   - Do not write to the target repository's tracked branches, and never
     commit or push to it directly. The one exception is the conversational
     metadata/probe authoring flow in Principle 8, which writes only inside
     an isolated worktree and stops at a reviewable diff/PR — the developer
     performs the final apply/merge into the real repository.

6. Limit deterministic decisions to explicit finite sets.
   - Deterministic rules are allowed only when the result belongs to a small,
     explicitly enumerated set or is direct structural validation.
   - Examples: file kind, known decorator presence, status transitions, exit
     code success/failure, schema validation, exact safety denylist matches.
   - System understanding, Feature extraction, Feature-to-Code mapping, probe
     selection, unknown side-effect analysis, and experiment interpretation
     require an external reasoning-model LLM API.
   - Keyword scores, similarity, embeddings, and static matches may retrieve
     candidates, but must not become the final open-ended decision.
   - If reasoning-model configuration, API calls, or structured-output
     validation fail, fail the run. Never fall back to heuristic inference.

7. Keep reasoning auditable.
   - Persist provider, model, prompt version, schema version, decision method,
     source snapshot, timestamps, and failure details for every intelligence run.
   - Decision method must be one of `deterministic`, `reasoning_llm`, or `manual`.
   - Mock LLM output is test/local-smoke data and must be visibly marked as mock.
   - LLM recommendations never directly approve, adopt, merge, or deploy changes.

8. Isolate all source changes and execution.
   - Instrumentation and source variants run in temporary worktrees/workspaces.
   - Commands must come from explicit repository configuration.
   - Network is off by default; environment variables are allowlisted.
   - Preserve reviewable patches and deterministic raw results.
   - A conversational system-understanding interview (purpose / capability /
     element discovery with the developer) may propose `probe-agent:`
     docstring metadata and probe instrumentation together. Proposed text is
     LLM-authored and stays `reasoning_llm` until a human approves it; once
     approved, probe-agent materializes the docstring edits and probe
     instrumentation in the same isolated worktree used for Probe Plans
     (Issue #25) and produces a single reviewable diff or pull request.
   - probe-agent never commits or pushes the result to the target
     repository's tracked branches itself. The developer reviews the diff/PR
     and performs the merge — this keeps the "no direct write to target
     repo" boundary (Principle 5) while removing manual transcription work.
   - This does not relax Principle 6: which capability an element belongs to,
     what its role/probe value is, and what to instrument remain
     reasoning-model proposals, never heuristic free-text guesses, and the
     developer's approval is the `decision_method: manual` record, not the
     LLM's output alone (Principle 7).

---

## Required Workflow Before Code Changes

Before modifying code, always check whether the requested change requires updates to:

- `CLAUDE.md`
- `.claude/skills/*/SKILL.md`
- shared schemas
- docs
- tests
- example app

If any instruction, workflow, schema rule, or recurring implementation pattern changes, update the relevant `CLAUDE.md` or `SKILL.md` first, then proceed with the implementation.

For issues #23-#26, always load:

- `.claude/skills/project-intelligence/SKILL.md`
- `.claude/skills/reasoning-llm/SKILL.md` when any non-finite inference is involved
- the area-specific skills for Control Server, Dashboard, schema, and testing

Read the owning GitHub issue and `docs/project-intelligence.md` before coding.
Treat later issues as non-goals unless the current issue explicitly expands scope.

If the change affects behavior, add or update tests unless there is a clear reason not to. If tests are not added, explain why.

---

## Testing Policy

Use tests to protect the expected behavior of the MVP.

Required test coverage:

- `@probe` preserves original return values
- `@probe` preserves original exceptions
- tracing failure does not break the wrapped function
- environment variable can disable the probe
- policy `off` skips tracing/control behavior
- policy `trace` records input/output/error/duration
- policy `shadow` returns current output while recording candidate output
- schema changes are validated against examples
- repository snapshots exclude uncommitted and untracked contents
- evidence locations resolve against the pinned snapshot
- reasoning-required operations do not use heuristic fallback
- reasoning run metadata and failures are persisted
- target repositories remain unchanged after worktree/experiment operations
- deterministic raw metrics remain available when interpretation fails

Do not rely only on manual testing when behavior can be covered by unit tests.

---

## Implementation Constraints

- Prefer small, focused changes.
- Keep interfaces explicit.
- Use typed models where reasonable.
- Avoid remote arbitrary code execution.
- Avoid hidden mutation of inputs and outputs in MVP.
- Do not introduce production replacement behavior unless explicitly requested in a future phase.
- Document any new environment variables.
- Update examples when public usage changes.
- Do not add speculative DB tables for later roadmap phases.
- Add persistence in the issue that owns the lifecycle and query requirements.
- Prefer additive SQLite schema changes; include migration/backfill behavior and
  isolation tests for every System-scoped table.
- Keep raw deterministic facts separate from LLM interpretations in storage.

---

## Verification Checklist

Before finishing a task, run the relevant checks when available:

- Python tests for modified packages
- Type or lint checks if configured
- Example app smoke test if SDK/server behavior changed
- Manual verification notes for dashboard-only changes

Summarize what was changed, what was tested, and any remaining risks.
