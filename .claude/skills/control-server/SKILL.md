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

## System State Assessment (issue #193)

- `GET /system-state` (`app/system_state.py`, `routes/system_state.py`)
  returns the normalized, deterministic, LLM-free state model: a list of
  `StateItem` (`state_id`, `state_group`, `severity`, `status`,
  `user_action_kind`, `intervention_timing`, `subject`, `summary`, `detail`,
  `impact`, `remediation`, `evidence`, `target_ui`, `related_checks`,
  `related_pipeline_steps`) plus `overall_severity` / `severity_counts`.
  Phase 1 scope: snapshot readiness, an in-progress interview session pinned
  to a stale snapshot, System Purpose / Core Capabilities state, and the
  symbol/entrypoint/documentation/capability-hierarchy pipeline steps.
- `system_state.py` is the single home for the System Purpose / Core
  Capabilities baseline-reuse and diff-impact logic
  (`evaluate_understanding`, `understanding_baseline`,
  `baseline_diff_impact`). `system_diagnostics._check_system_purpose` /
  `_check_system_capabilities` call `evaluate_understanding` to build their
  `DiagnosticCheck` text — do not re-implement baseline/diff logic in
  `system_diagnostics.py`; extend `system_state.py` instead so Diagnostics
  and the Assistant screen context (which reads diagnostics checks) keep
  sharing one evidence source.
- Deterministic only (Principle 6): no reasoning-model call, derived solely
  from persisted DB rows for the latest ready snapshot. A missing/blocked
  state is represented explicitly (e.g. `understanding.purpose.missing_baseline`,
  `pipeline.capability_hierarchy.blocked_by_reasoning`) rather than guessed.
- Pipeline state ids must name the actual state. Use `.not_run` only for
  missing/cancelled retryable work, `.running` for queued/running builds or
  steps (`status=running`, `severity=info`, `user_action_kind=wait`),
  `.failed` for failed persisted runs (`severity=error`,
  `user_action_kind=rerun`), and `.blocked` / `.blocked_by_reasoning` for
  blocked work (`severity=blocked`). Do not collapse pending/running/blocked
  into failed, and do not use `blocked_by_reasoning` for ordinary not-run or
  failed states when a reasoning model is available.
- A step whose latest run is `completed` but whose deterministic artifact
  count is zero (e.g. `pipeline.capability_hierarchy.empty`, mirroring
  `system_understanding_service._check_documentation_indexed`'s "completed
  but zero chunks" warning) is not "done": return a distinct
  `.<empty-kind>` state item (`severity=warning`, `status=missing`) instead
  of `None`, and point `remediation` at fixing the input (e.g. Interview /
  source metadata) rather than the generic "Build / Refresh を実行してくだ
  さい" used for not-yet-run/failed/blocked steps — the build already ran.
- `GET /system-diagnostics` stays backward compatible; it is a projection
  built on top of `system_state.py`, not replaced by it.
- Later phases (not yet implemented): projecting `next_actions` and
  Dashboard page callouts/toasts from the same state items, and covering
  the `runtime` / `proposal` / `interview` (beyond the one stale-snapshot
  item) state groups.

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
- An informational branch (severity `unknown`, e.g. `llm_last_run`'s "no
  reasoning run recorded yet") must say so in its own text: it must not
  present itself as the cause of other warning/error checks, its remediation
  must name the operations that actually change its state (build claim scan /
  draft generation / Interview dialogue — not a generic "run Build"), and it
  must carry `related_pipeline_steps` for the steps it actually gates so
  consumers can scope and rank it below actionable checks sharing the same
  `fix_anchor`. The Dashboard resolves anchor-only deep links by severity,
  so a misleading generic branch would otherwise win the fix callout.

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
- `claim_scan` chunk reuse (`_run_claim_scan`) matches on `system_id` +
  `chunk_path` + `chunk_content_hash` + `prompt_version` + `schema_version`
  + completed status with a non-null `result_json` — deliberately not
  `snapshot_id` and not `chunk_id` (Issue #195). This means an unchanged
  documentation chunk is reused across a Refresh's new `snapshot_id` (same
  content hash), while a chunk whose `chunk_id` is unchanged but whose text
  changed still gets rescanned (hash differs). Because `result_json` embeds
  absolute evidence line numbers and the source `chunk_id`, reuse rewrites
  the result for the current chunk: `chunk_id` is replaced and evidence
  start/end lines are offset by the start-line delta against the stored
  `chunk_start_line` (a structural shift for byte-identical text), so
  evidence keeps resolving against the pinned current snapshot. Legacy rows
  without `chunk_start_line` are only reused when `chunk_id` matches
  exactly. Chunks absent from the current snapshot's documentation index
  never get a pending task row for the current build, so deleted sections
  cannot leak into the new build's `understanding_graph` via this reuse
  path.
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

## GitHub App publish workflow (issue #216)

- `app/github_app.py` (JWT signing + Installation Token broker + a few
  read-only/write GitHub REST calls: `get_repository`,
  `list_installation_repositories`, `create_pull_request`,
  `list_open_pull_requests_for_branch`), `app/repo_manager.py` (managed
  mirror clone/fetch under `GIT_REPOSITORY_ROOT`, per-job worktrees,
  `connection_lock`, cleanup), `app/publish_job.py` + `app/publish_guards.py`
  (the two-phase publish state machine and its safety checks), routes in
  `routes/github_connections.py` and `routes/publish_jobs.py`. See the
  "GitHub App 公開ワークフロー（Issue #216）" section of
  `docs/project-intelligence.md` for the full state diagram and safety
  boundaries — do not duplicate that design narrative here.
- An installation token, the App JWT, and the private key must never reach a
  database row, log line, or API response. Always route any exception text
  that might embed one through `github_app._sanitize` before it is persisted
  or returned; `publish_jobs.error` is stored pre-sanitized so routes may
  return it verbatim.
- `GET /github/installations/{installation_id}/repositories` (sub-task 4) is
  read-only and uses the same `_require_manage` (admin or System owner)
  authorization as connection management; it returns
  `GithubInstallationRepositoryOut` (owner/name/default_branch/private only,
  never a token) and 502s with a sanitized message on `GitHubAppError`.
- The push target is always a server-generated `probe/`-prefixed branch
  (`publish_guards.generate_branch_name` / `validate_push_target`); force
  push and direct push to the base/default branch are not implemented in
  this MVP regardless of `GIT_ALLOW_DIRECT_PUSH` / `GIT_ALLOW_FORCE_PUSH`
  (read but never honored as `true`).
- `create_publish_job` requires the connection to be `status=connected` and
  the patch's latest `baseline` + `probed` validation runs to both be
  `overall_success=true` — this reuses Issue #25's validation gate rather
  than adding a second one. The remote base-branch SHA is re-resolved and
  compared against the patch's pinned commit twice (entering `fetching` and
  again immediately before `pushing`); any mismatch fails the job closed
  with no auto-rebase.
- Only files present in the patch diff are staged (`git add`), each
  structurally validated by `publish_guards.validate_patch_file_path`
  (rejects paths outside the diff, `.git/`, path traversal, symlinks, secret-
  name candidates, and `.github/workflows/` unless
  `GIT_ALLOW_WORKFLOW_CHANGES=true`).
- Approval only moves `awaiting_approval -> committing`; publishing
  (commit/push/PR) only ever starts from an explicit `POST
  /github/publish-jobs/{id}/approve` call. probe-agent never merges or
  closes the resulting Pull Request itself.
- Every terminal state (`completed`/`failed`/`cancelled`) cleans up the job
  worktree and records `cleanup_state`/`cleanup_error`. Tests for this whole
  area live in `tests/test_github_app.py` (App/connection layer, including
  the installation-repositories endpoint) and `tests/test_publish_jobs.py`
  (state machine, staleness, push safety, diff-path guards, idempotency,
  cleanup, system isolation, secret hygiene).
- **Compose secret + `GITHUB_PUBLISH_ENABLED` startup validation (Issue
  #224)**: `docker-compose.prod.yml` mounts the private key as a Docker
  Compose secret (`github_app_private_key`, file path from
  `GITHUB_APP_PRIVATE_KEY_HOST_PATH`, default `/dev/null`) and fixes
  `GITHUB_APP_PRIVATE_KEY_PATH` in-container to
  `/run/secrets/github_app_private_key` — never a host path. `GITHUB_PUBLISH_ENABLED`
  (finite set `{"", true, false, 1, 0, yes, no, on, off}`, case-insensitive;
  anything else fails startup) is the declared-intent switch:
  `github_app.validate_publish_startup_config()`, called from
  `db.init_db()` right after `_validate_startup_environment()` (Issue #225's
  pattern), raises `RuntimeError` when it is true but `GITHUB_APP_ID` is
  empty, or `GITHUB_APP_PRIVATE_KEY_PATH` does not point at a readable,
  non-empty file that parses as a PEM private key
  (`cryptography...load_pem_private_key`). Error messages name only the env
  var, never the key bytes or the path value. `github_app_configured()`
  also now requires the key file to be non-empty (`os.path.getsize(...) > 0`)
  so the `/dev/null`-default secret reads as "not configured" instead of
  failing later inside JWT signing. See
  `docs/github-app-deployment.md` for the deployment runbook (registration,
  host placement, rotation).
- **Disconnect revokes publish permission immediately (Issue #227)**:
  `routes/github_connections.py::delete_connection` cancels every
  non-terminal publish job of a connection (prepare phase or
  `awaiting_approval`) in the same transaction as the disconnect, and
  refuses (409) if a job is already in an in-flight publish phase
  (`committing`/`pushing`/`creating_pr`). `approve_publish_job`'s
  compare-and-set requires the connection to still be `connected`, and
  `publish_job._require_connection_still_connected` re-checks at phase
  entry and immediately before the push, on top of
  `_require_publish_installation_assignment`'s existing check right before
  every token issuance. `verify_connection` / `sync_connection` /
  `create_publish_job` all 409 on a disconnected connection; reconnect is
  always a new `github_connections` row. Audit events (append-only
  `publish_audit_events`, `app/publish_audit.py`) never carry a token or
  filesystem path. See the "Disconnect 時の即時失効(Issue #227)" subsection
  of `docs/project-intelligence.md` for the full design; tests live in
  `tests/test_publish_disconnect.py`.
- **Publish job retry/recovery (Issue #226)**: a post-approval failure rests
  in `retryable_failed` (or `manual_intervention_required` if the remote
  branch exists but does not match the job's recorded commit) instead of
  always dead-ending at terminal `failed` -- stale-base-branch conflicts and
  a mid-flight disconnect (`ConnectionRevokedError`) are the only
  post-approval failures that still stay terminal `failed`. `POST
  /github/publish-jobs/{id}/retry` (`publish_job.retry_publish_job`) and the
  periodic worker's `auto_retry_eligible_jobs` (capped by
  `PUBLISH_AUTO_RETRY_MAX`, `manual_intervention_required` never included)
  both compare-and-set the job to `reconciling` and run
  `publish_job._run_reconcile_phase`, which re-derives the next step from
  the actual remote branch/commit state under the same job id and the same
  server-generated branch -- never a new branch, never a force push. A
  DB-backed lease (`publish_connection_leases`) guards a connection across
  process restarts on top of `repo_manager.connection_lock`'s in-process
  lock. `app/publish_recovery.py` also fails over jobs whose worker thread
  died (stale `heartbeat_at`) at startup and on a periodic tick. Every
  status transition is recorded append-only in `publish_audit_events` in
  the same transaction that performs it (`GET
  /github/publish-jobs/{id}/events` reads it back). See
  `docs/project-intelligence.md`'s "Publish job の retry / recovery(Issue
  #226)" subsection for the full reconcile decision table; tests live in
  `tests/test_publish_retry.py`.

## Authentication and user management

- Auth is enabled when any user exists or `CONTROL_API_KEYS` is set; otherwise open (MVP compat).
- Initial admin is bootstrapped from `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` at startup.
- `CONTROL_REQUIRE_AUTH` (default `false`, issue #217) is checked in
  `app/db.py::_enforce_auth_requirement`, called from `init_db()` right after
  admin bootstrap. When `true` and auth still cannot be enabled (no admin
  user, no `CONTROL_API_KEYS`), startup fails closed with `RuntimeError`
  instead of silently running open; `docker-compose.prod.yml` sets this to
  `"true"`. When `false` (local/dev default), the fail-open MVP-compat
  behavior is unchanged but a warning is logged. See
  `docs/deployment-https.md`.
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
- `require_user` / `require_admin` reject `token_kind == "api"` unconditionally
  (403, "SDK API tokens cannot access management APIs; use a login session"),
  not just in production: SDK API tokens are for data-plane routes (traces,
  policies, ...) that depend on `get_principal`/`get_system_id` only, never
  management/connection/publish routes.
- `CONTROL_ENV` (`app/environment.py`, Issue #225; default `development`,
  finite set `{development, production}`, anything else fails startup)
  drives a strict fail-closed production mode checked in `app/db.py`
  (`_validate_startup_environment`, before `_bootstrap_admin`, and the
  production branch of `_enforce_auth_requirement`, after). In production:
  `CONTROL_REQUIRE_AUTH` is forced on (explicitly disabling it fails
  startup); `CONTROL_API_KEYS` must be empty (legacy keys are forbidden --
  `auth.auth_enabled()`/`auth._legacy_keys()` also force this at runtime,
  independent of startup validation); `CONTROL_ADMIN_PASSWORD` (when
  `CONTROL_ADMIN_USERNAME` is also set) must pass
  `security.validate_production_password` (>=16 chars, not in the
  case-insensitive sample-value denylist, not equal to the username) even
  if the admin row already exists from an earlier boot; and startup fails
  unless at least one active admin user exists afterward. `create_user` /
  `reset_password` (`routes/auth.py`) apply the same password validator in
  production, returning 422 with the reason. Development is unchanged
  (permissive, warning-only). A successful env-bootstrap of the admin user
  writes one append-only `auth_audit_events` row
  (`event_type='admin_bootstrapped'`); `detail` is structural JSON only,
  never a password or hash.

## Probe Pattern lifecycle (issue #168)

- Routes live in `routes/probe_patterns.py`; core logic in
  `instrumentation_remover.py` (removal patches) and `pattern_reconciler.py`
  (classification). Tables: `probe_patterns`, `probe_pattern_points`,
  `probe_pattern_events`, `probe_pattern_reconciliations`,
  `probe_pattern_reconcile_points`, `probe_removal_patches` — all
  system-scoped.
- `GET /repository/probe-instrumentation` is a deterministic scan of the
  latest indexed snapshot for `@probe`-decorated symbols (decorator presence
  from `code_symbols` is a structural fact). Each hit links back to its
  probe plan point and patterns so removal keeps its context.
- Saving a pattern captures structural facts from the pinned snapshot:
  extracted signature, `symbol_source_hash` / `symbol_body_hash`, docstring,
  line range, and the source commit. These make later `exact_match` /
  `changed_signature` reconcile decisions deterministic.
- Removal patches mirror instrumentation patches: generated in an isolated
  worktree, reviewable diff, applied only via the explicit
  commit-sha-confirmed endpoint against a clean tree. A successful apply
  marks the covered points `removed_from_production`.
- Reconciliation classification splits per Principle 6. Deterministic:
  `exact_match` (same path+symbol, same extracted signature),
  `changed_signature` (same path+symbol, different signature), `unsafe`
  (denylist), and verbatim relocation (identical body hash at exactly one
  new location → `moved_match`). Everything else (`moved_match`,
  `split_or_merged`, `missing`, non-verbatim renames) requires the reasoning
  model with candidate retrieval as hints only; LLM failure fails the run
  (`pattern_reconcile` intelligence run) while deterministic points stay
  persisted. Never fall back to heuristics.
- LLM reconcile output is strictly validated: classifications from the
  finite set only, targets must be indexed symbols, evidence must reference
  snapshot paths, and denylist hits on resolved targets override to
  `unsafe`.
- Reconcile decisions are per-point manual records
  (`accepted` / `rejected`); `unsafe` and `missing` can never be accepted.
  The "I don't know" flow calls `POST
  /pattern-reconcile-points/{id}/investigate` (run_type
  `pattern_investigate`), which reads bounded excerpts from the pinned
  snapshot only.
- `create-plan` converts a completed latest reconciliation into a normal
  probe plan (origin `probe_pattern`, run_type `probe_plan_from_pattern`,
  decision_method `manual`): exact matches automatically, non-exact points
  only when accepted. Re-attachment then reuses the existing plan → approve
  → patch → validate → apply gates; never add a shortcut apply path.
- Pattern status is a finite set (`active` / `stale` / `archived` /
  `superseded`): a completed reconcile sets `active` (all exact) or `stale`
  (any non-exact); archive/restore are manual. Lifecycle events are
  append-only rows in `probe_pattern_events`.

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
- GitHub publish workflow: no installation token/JWT/private key ever
  persisted or returned, `probe/`-branch + no-force-push enforcement, the
  Issue #25 validation gate at job creation, the pre-push staleness
  re-check, approve/cancel idempotency, worktree cleanup on every terminal
  state, and system isolation
