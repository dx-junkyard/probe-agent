---
description: Use when implementing or modifying Control Server APIs, persistence, repository intelligence, reasoning runs, traces, policies, components, generation, and experiments.
---

# Control Server Skill

## Scope

Use this skill for files under:

- `apps/control-server/`
- `shared/schemas/` when API contracts change

## Required APIs for MVP

- `POST /traces`
- `GET /components`
- `GET /components/{component_id}/traces`
- `GET /components/{component_id}/policy`
- `PUT /components/{component_id}/policy`
- `POST /components/{component_id}/shadow-results`

## Evaluation context APIs (issue #9)

- `GET /system-profile`, `PUT /system-profile` (singleton, id `default`)
- `GET /components/{component_id}/profile`, `PUT /components/{component_id}/profile`
- `GET /components/{component_id}/criteria`, `POST /components/{component_id}/criteria`
- `PUT /criteria/{criterion_id}`
- `POST /traces/{trace_id}/evaluate`, `GET /traces/{trace_id}/evaluations`

The Evaluation Context criterion engine is rule-based only (`app/evaluator.py`);
do not call an LLM from that criterion engine.
`exact_match` / `contains` / `regex` / `json_equal` / `required_keys` are decided
deterministically; `natural_language` is always recorded as `needs_review`.
Re-evaluating a trace replaces its prior results (idempotent).

This restriction applies to the finite evaluation-criterion engine only.
Feature Intelligence has different requirements: open-ended understanding,
mapping, planning, and interpretation must call a reasoning model through the
provider-neutral LLM layer. Do not reuse `app/evaluator.py` as a heuristic
fallback for intelligence work.

## System settings diagnostics (issue #101)

- `GET /system-diagnostics` (`app/system_diagnostics.py`, `routes/diagnostics.py`)
  returns deterministic, LLM-free health checks for required configuration:
  env presence, enum membership, path existence and read/write permission,
  provider/model-family consistency, reasoning-capability, and pipeline
  prerequisites.
- Severity vocabulary: `ok | warning | error | blocked | unknown`. Every
  check carries impact, remediation, related env/paths/pages/pipeline steps,
  and `decision_method: deterministic`.
- Runtime-only failures (LLM timeout/auth/invalid model, snapshot failures)
  are surfaced verbatim from the latest persisted run/snapshot records as
  `last_observed_error`. Never classify or interpret error text with
  heuristics, and never call an LLM from a diagnostics check.
- New required settings must be added here with title/impact/remediation so
  the Dashboard alert badge can explain them.

## Feature Intelligence APIs (issues #23-#26)

The current `GET /project-intelligence` response is a mock contract.

- #23 owns repository configuration, snapshots, evidence-backed drafts, and
  intelligence-run persistence.
- #24 owns code symbols and Feature-to-Code links.
- #25 owns Probe Plans, temporary instrumentation patches, and validation runs.
- #26 owns experiments, variants, artifacts, metrics, and interpretations.

Only add tables needed by the current issue. Every new table must be scoped by
`system_id` where applicable and have explicit lifecycle/query tests.

Keep these storage concerns separate:

- immutable or reproducible deterministic facts: snapshot, file metadata,
  symbols, command results, raw metrics
- reasoning outputs: drafts, links, plans, interpretations
- audit metadata: provider, model, prompt/schema version, decision method,
  source snapshot, timestamps, error
- manual decisions: accepted/rejected/adopted notes

Reasoning failure must be represented as a failed run. Do not synthesize a
heuristic result.

## Authentication and user management

- Auth is enabled when any user exists or `CONTROL_API_KEYS` is set; otherwise open (MVP compat).
- Initial admin is bootstrapped from `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` at startup.
- Passwords are hashed with PBKDF2-HMAC-SHA256 (`app/security.py`); never store plaintext.
- Tokens are random (`secrets.token_urlsafe`) and stored only as SHA-256 hashes in `api_tokens`.
- Accept credentials via `Authorization: Bearer <token>` or `X-Api-Key: <token>`.
- Resolve the caller with `get_principal`; guard admin endpoints with `require_admin`.
- Admin-only: create/list users, deactivate/delete users, reset passwords
  (`POST /users/{id}/password`), change roles (`PUT /users/{id}/role`), and
  issue/list/revoke any token (`GET/POST /tokens`, `POST /tokens/{id}/revoke`).
- Self-service token endpoints require a user principal (`require_user`):
  `GET /tokens/me`, `POST /tokens/me`, `POST /tokens/me/{id}/revoke`.
  Legacy API keys and anonymous callers get 403; revoking a token owned by
  someone else returns 404.
- `POST /auth/logout` revokes the calling token (no-op for legacy keys).
- Deactivating a user must revoke their tokens. Resetting a password must
  revoke the user's session tokens (API tokens stay valid).
- Role changes must not demote the last active admin (409).
- Revoked/expired/inactive tokens return 401.

## Rules

- Validate incoming payloads.
- Keep API models aligned with shared schemas.
- Store trace events with component_id, input, output, error, duration, timestamp.
- Policy defaults should be safe:
  - unknown component: `trace` or `off`, depending on current MVP decision
  - server error must not imply replace behavior
- Never expose arbitrary code execution endpoints.
- Never log or return raw tokens/passwords; raw tokens are shown only once on creation.
- Repository paths must not permit reads outside the configured Git repository.
- Never read target source directly from the mutable working tree.
- Commands must come from explicit configuration, run in an isolated workspace,
  and enforce timeout/network/environment policies.
- Deterministic safety denylists override LLM output.

## Required Tests

Add or update tests for:

- trace ingestion
- invalid payload handling
- component listing
- policy read/update
- shadow result ingestion
- schema compatibility
- auth: login/logout, authenticated user, admin-only access, token issue/revoke, deactivation
- self-service tokens: issue/list/revoke own tokens, cannot touch other users' tokens,
  legacy key / anonymous rejected
- password reset and role change permissions and guards
- System isolation for every intelligence table/API
- committed-only snapshot behavior
- reasoning-required operations fail closed without heuristic fallback
- reasoning metadata and structured-output validation
- target repository unchanged after workspace operations
