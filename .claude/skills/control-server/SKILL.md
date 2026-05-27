---
description: Use when implementing or modifying the Control Server APIs for traces, policies, components, and shadow results.
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

Evaluation is rule-based only (`app/evaluator.py`); never call an LLM in the MVP.
`exact_match` / `contains` / `regex` / `json_equal` / `required_keys` are decided
deterministically; `natural_language` is always recorded as `needs_review`.
Re-evaluating a trace replaces its prior results (idempotent).

## Authentication and user management

- Auth is enabled when any user exists or `CONTROL_API_KEYS` is set; otherwise open (MVP compat).
- Initial admin is bootstrapped from `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` at startup.
- Passwords are hashed with PBKDF2-HMAC-SHA256 (`app/security.py`); never store plaintext.
- Tokens are random (`secrets.token_urlsafe`) and stored only as SHA-256 hashes in `api_tokens`.
- Accept credentials via `Authorization: Bearer <token>` or `X-Api-Key: <token>`.
- Resolve the caller with `get_principal`; guard admin endpoints with `require_admin`.
- Admin-only: create/list users, deactivate users, issue/list/revoke tokens.
- Deactivating a user must revoke their tokens. Revoked/expired/inactive tokens return 401.

## Rules

- Validate incoming payloads.
- Keep API models aligned with shared schemas.
- Store trace events with component_id, input, output, error, duration, timestamp.
- Policy defaults should be safe:
  - unknown component: `trace` or `off`, depending on current MVP decision
  - server error must not imply replace behavior
- Never expose arbitrary code execution endpoints.
- Never log or return raw tokens/passwords; raw tokens are shown only once on creation.

## Required Tests

Add or update tests for:

- trace ingestion
- invalid payload handling
- component listing
- policy read/update
- shadow result ingestion
- schema compatibility
- auth: login, authenticated user, admin-only access, token issue/revoke, deactivation
