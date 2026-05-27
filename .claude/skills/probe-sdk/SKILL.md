---
description: Use when implementing or modifying the Python Probe SDK, including @probe, policy handling, trace sending, and shadow execution.
---

# Python Probe SDK Skill

## Scope

Use this skill for files under:

- `packages/python-probe/`
- `examples/simple-pipeline/` when validating SDK behavior

## Rules

- `@probe` must preserve the wrapped function's normal return value.
- `@probe` must preserve wrapped function exceptions.
- Probe failures must not break the target application.
- If Control Server is unavailable, run the original function normally.
- `shadow` mode must return the current implementation output.
- Candidate output must only be recorded for comparison.
- Do not implement production `replace` behavior in MVP.

## Required Tests

Add or update tests for:

- normal return preservation
- exception preservation
- disabled probe
- trace mode
- server failure fallback
- shadow mode behavior
- candidate failure handling

## Verification

Run the package tests before finishing.
