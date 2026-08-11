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
- Feature Intelligence may propose instrumentation, but SDK code and target
  source must only be changed in an approved isolated worktree. An LLM plan
  must never weaken the SDK's fail-open host-application guarantees.
- Replay capture (Issue #242 Phase A / #243) is opt-in per component via
  `@probe(..., replay_capture=True | {"redact": [...]})`
  (`probe_agent/replay_capture.py`). It captures a canonical JSON,
  round-trippable form of the call inputs plus a deterministic
  `replayability` classification (finite set + reason codes). It must stay
  best-effort: capture failure never changes the wrapped function's return
  value, exceptions, or trace sending, and opt-out adds zero overhead and no
  new trace keys. Redaction reuses the projection `redact` grammar
  (fail-closed); size is bounded by `PROBE_REPLAY_CAPTURE_MAX_BYTES`
  (a too-large or unmaskable capture is dropped, never truncated). The
  existing repr `input`/`output` fields are unchanged.

- Redaction (Issue #367) is two mandatory layers, and both live in the SDK:
  `redaction.py` masks by key name — including **object attribute names**, via
  `redact_for_repr`, which the repr payload path uses instead of
  `redact_sensitive` — and `secret_patterns.py` masks documented credential
  *shapes* in the rendered text. Every payload string leaves through
  `decorator._payload_repr` or `decorator.redact_text`; do not add a new path
  that reaches `repr()` directly. `redact_text` masks before truncating, so a
  secret cannot survive by straddling the 4000-character boundary. Redaction
  failure degrades to `<payload-redaction-failed>` — never to raw text, and
  never to an exception reaching the host call. `PROBE_PAYLOAD_MODE=full`
  still applies both layers.
- `secret_patterns.py` is imported by the Control Server too
  (`app/trace_redaction.py`), so it is a cross-package contract: adding a rule
  changes both boundaries at once, and the rule set must stay finite and
  vendor-documented (Principle 6 — no entropy scoring).

## Required Tests

Add or update tests for:

- normal return preservation
- exception preservation
- disabled probe
- trace mode
- server failure fallback
- shadow mode behavior
- candidate failure handling
- redaction: a secret must not appear in the rendered payload for any of
  a plain dict, a dataclass, a `__slots__` object, an object with a custom
  `__repr__`, an uninspectable object, or a nested/recursive graph

## Verification

Run the package tests before finishing.
