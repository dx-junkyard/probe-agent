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
```

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

---

## Verification Checklist

Before finishing a task, run the relevant checks when available:

- Python tests for modified packages
- Type or lint checks if configured
- Example app smoke test if SDK/server behavior changed
- Manual verification notes for dashboard-only changes

Summarize what was changed, what was tested, and any remaining risks.
