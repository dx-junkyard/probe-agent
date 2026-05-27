---
description: Use when adding tests, fixing failing tests, or deciding what tests are required for a change.
---

# Testing Skill

## Test Strategy

Prefer focused tests close to the changed behavior.

For Probe SDK:

- unit tests for decorator behavior
- fallback tests for server failure
- shadow mode tests
- serialization tests

For Control Server:

- API tests
- validation tests
- persistence tests
- policy tests

For Dashboard:

- minimal smoke tests if framework supports it
- otherwise provide manual verification steps

## Required Decision

For every behavior change, either:

- add or update tests, or
- explicitly explain why tests are not practical for this change

## Completion

Report:

- tests added
- tests run
- tests skipped
- known gaps
