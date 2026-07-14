---
description: Use when changing shared schemas, trace payloads, policy definitions, or cross-package contracts.
---

# Schema Change Skill

## Scope

Use this skill when changing:

- `TraceEvent` (incl. the Issue #243 additive replay fields
  `input_capture` / `replayability` / `replay_reasons`)
- `ControlPolicy`
- `ShadowResult` (now contracted in `shared/schemas/shadow_result.schema.json`,
  added in Issue #243 — keep it consistent with the server model and the SDK
  shadow payload)
- component_id rules
- mode definitions
- API payloads
- SDK/server/dashboard contract
- repository snapshots and evidence
- Feature profiles and code links
- Probe Plans and experiment contracts
- reasoning-run audit metadata

## Required Steps

1. Update shared schema files.
2. Update Python SDK models or serializers.
3. Update Control Server models.
4. Update Dashboard usage.
5. Update example payloads.
6. Update tests.
7. Update docs if public behavior changed.
8. Classify fields as deterministic fact, reasoning output, audit metadata, or
   manual decision; do not conflate them.
9. Update mock fixtures and mark them explicitly as mock.

## Compatibility

Prefer backward-compatible changes during MVP unless there is a strong reason.

For intelligence schemas:

- include `system_id` or otherwise prove System ownership
- include snapshot/commit provenance
- include `decision_method`
- include provider/model/prompt/schema version for reasoning outputs
- represent failed reasoning runs without generating fallback content
- use enums only for small, explicit finite sets

If making a breaking change, document:

- what changed
- why it changed
- which files were updated
- migration notes if needed
