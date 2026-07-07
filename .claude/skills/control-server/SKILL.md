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
- Issue #115: user-facing text (title/detail/impact/remediation) is Japanese
  and framed as 原因 / 修正場所 / 次の操作. Each check also carries a
  deterministic fix target: `fix_kind` (finite `navigate | dialog`),
  `fix_page` (a Dashboard route), and `fix_anchor` (a member of the finite
  anchor set: `repo-config`, `repo-patterns`, `snapshot-create`, `build`).
  `navigate` means an in-app control fixes it; `dialog` means it is an env
  var / restart with no in-app control. These are chosen structurally per
  branch — never inferred — and must be kept in sync with the `diag-anchor`
  attributes rendered by the Dashboard. Identifiers embedded in `detail`
  (env var names, model ids, paths) stay verbatim.

## Per-screen assistant (issue #102)

- `GET /assistant/settings-metadata`, `GET /assistant/screen-context/{screen_id}`,
  `POST /assistant/ask` (`app/assistant.py`, `app/settings_metadata.py`,
  `routes/assistant.py`).
- Settings explanations are static code-managed metadata in
  `app/settings_metadata.py` (key, requiredness, valid values, description,
  impact, remediation, related checks/pages/pipeline steps) — never LLM
  generated. Every env var referenced by a diagnostics check's `related_env`
  must have a metadata entry (enforced by tests).
- Screen contexts in `app/assistant.py` are a static registry keyed by
  dashboard route segment (`overview`, `system-understanding`, ...). Adding a
  dashboard page means adding its screen context here.
- `POST /assistant/ask` grounds every answer in a limited, deterministic
  context pack: the screen context, settings metadata mentioned in the
  question (finite key matching only), and the current `system_diagnostics`
  checks. Only this pack is sent to the LLM.
- LLM answers (`decision_method: reasoning_llm`, `used_fallback: false`) are
  strict-JSON validated; citations and navigate/operate targets outside the
  supplied pack/route set are dropped (structural validation — `operate`
  targets are routes where the operation is performed, never bare operation
  names). The API key must match the effective provider
  (`LLMConfig.intelligence_from_env`), same rule as the diagnostics
  `_api_key_status`; a mismatched key means no external call. Provider
  `mock`, a missing/mismatched key,
  LLM errors, or invalid output all switch to the deterministic fallback
  composed verbatim from the metadata/diagnostics above
  (`decision_method: deterministic`, `used_fallback: true`, with
  `llm_error` populated on failure). The fallback never interprets free text
  beyond finite-set matching against known setting keys, check ids/titles,
  and pipeline steps.
- Assistant Q&A is not persisted (no chat tables); audit metadata
  (provider/model/prompt/schema version, decision method, failure detail) is
  returned in the response instead.

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

## System Understanding build jobs (issue #109)

- `POST /repository/system-understanding/build` enqueues a step-orchestrated
  background job (`app/system_understanding_jobs.py`) and returns the job id
  immediately (202). Never run build steps inside a request handler.
- Job/step state lives in `system_understanding_builds` /
  `system_understanding_build_steps`; claim-scan chunks are
  `system_understanding_llm_tasks` rows with unified retry/backoff/cancel.
- Jobs bind to the snapshot they were created with: retry/resume always
  runs against the job's stored `snapshot_id`, never the latest ready
  snapshot. Only a job created without any ready snapshot binds to the
  latest one at execution time; a pinned snapshot that disappeared fails
  the job with an explicit error.
- Steps: `symbol_index`, `entrypoint_index`, `documentation_index`,
  `claim_scan` (reasoning), `understanding_graph`, `docs_code_reconcile`,
  `capability_hierarchy`. Dependencies are explicit; a step whose dependency
  is not completed is `blocked`, never silently skipped or approximated.
- Completed steps are never re-executed. Retry
  (`POST .../jobs/{id}/retry`, `POST .../jobs/{id}/steps/{step}/retry`)
  resets only missing/failed/blocked/cancelled steps; completed chunk scan
  results are reused by content hash. Cancel is available per job and per
  step; workers check the flag between steps and between chunks.
- Jobs and steps persist heartbeats. A queued/running job without a recent
  heartbeat (`SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS`, default 300) is
  reported `is_stuck`; `init_db` fails over jobs interrupted by a restart so
  they become retryable instead of active-forever.
- LLM failures fail the `claim_scan` step visibly with per-chunk errors;
  deterministic steps still complete (no heuristic fallback, Principle 6).
- Job status vocabulary: `queued / running / completed / partial / failed /
  cancelled` (+ derived `is_stuck`). `completed` requires every step
  completed; any remaining failed/blocked/cancelled step yields `partial`
  (or `failed` when no step completed), so blocked reasoning steps never
  hide behind a completed job.
- Each worker execution (initial enqueue and every retry) is a
  `system_understanding_build_runs` row; the build endpoints return both
  `job_id` and the latest `run_id`.
- Status endpoints: `GET .../jobs/{job_id}`, `GET .../jobs/active`, plus the
  back-compat `GET .../build/latest` and `GET .../build/{id}` returning the
  same extended payload (steps, llm task counts, artifact counts).

## Issue drafts (issue #107)

- `POST /issue-drafts`, `GET /issue-drafts`, `GET /issue-drafts/{id}`,
  `PATCH /issue-drafts/{id}` (`app/issue_drafts.py`, `routes/project_intelligence.py`).
  probe-agent's DB (`issue_drafts` table, system-scoped) is the source of
  truth; external trackers are NOT integrated.
- A draft is generated from a System Understanding gap: rendering the gap's
  title, docs/code/entrypoint evidence, next actions, and the pinned
  `snapshot_id` / `commit_sha` into a Markdown body is a deterministic,
  structural template (Principle 6) — no reasoning model is called. Upstream
  gap detection is where reasoning happens.
- Gaps are recomputed per read (no stable id), so each gap carries a
  deterministic `source_key` (`gap_source_key`), and `GET
  /repository/system-understanding` attaches any matching drafts to each gap
  (`issue_drafts`), matched by that key against the caller's open connection
  (the DB lock is non-reentrant — never open a nested `get_conn`). The key
  folds in a stable per-gap `source_id` (graph node id / entrypoint identity)
  plus `capability_key` + docs/code/entrypoint evidence (hashed, order
  independent), not just `gap_type` + `node_name`, so same-named gaps in one
  system don't share drafts — including evidence-less gaps like
  `missing_evidence`, which are distinguished by `source_id` alone. `source_id`
  is on the gap output so it round-trips: a draft created from a POSTed gap
  resolves to the same key the display computed.
- `POST /issue-drafts` accepts the displayed `snapshot_id` / `commit_sha`;
  if a newer snapshot has since become ready the request is refused (409) so
  a draft never embeds a snapshot that disagrees with the gap evidence in the
  payload. Omitting it falls back to the latest ready snapshot.
- `status` vocabulary is a finite set: `draft / copied / external_created /
  closed / rejected` (validated; anything else is 422). `external_url` is a
  plain user-supplied string, validated only as `http(s)://`; probe-agent
  never writes to the target repository's tracked branches (Non-goals;
  Principle 5).
- `PATCH` uses field set-ness so `external_url: ""` clears a registered URL
  while omitting it leaves it untouched.
- `GET /issue-drafts/github-status` and `POST
  /issue-drafts/{id}/create-github-issue` (Issue #158, `github_integration.py`)
  add an optional GitHub path: availability is a finite, structural check
  (`GITHUB_TOKEN` set + owner/repo resolvable from the configured repo's
  `origin` remote or `GITHUB_REPO=owner/repo`, never guessed). When available,
  creating the issue calls the GitHub REST API directly and stores the
  returned `html_url` via the same `update_draft` external_url path a manual
  registration uses (status -> `external_created`). Unavailable or failed
  calls are a 422 with a reason; the manual copy/paste URL flow always stays
  available as a fallback. This is still not a target-repository write.

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
