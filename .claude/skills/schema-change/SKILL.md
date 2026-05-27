---
description: Use when changing shared schemas, trace payloads, policy definitions, or cross-package contracts.
---

# Schema Change Skill

## Scope

Use this skill when changing:

- `TraceEvent`
- `ControlPolicy`
- `ShadowResult`
- component_id rules
- mode definitions
- API payloads
- SDK/server/dashboard contract

## Required Steps

1. Update shared schema files.
2. Update Python SDK models or serializers.
3. Update Control Server models.
4. Update Dashboard usage.
5. Update example payloads.
6. Update tests.
7. Update docs if public behavior changed.

## Compatibility

Prefer backward-compatible changes during MVP unless there is a strong reason.

If making a breaking change, document:

- what changed
- why it changed
- which files were updated
- migration notes if needed
