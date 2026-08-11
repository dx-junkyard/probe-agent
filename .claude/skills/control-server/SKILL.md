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

## Trace ingestion redaction (Issue #367)

`POST /traces` redacts **before the row is written**, in
`app/trace_redaction.py`, reusing the SDK's rule tables
(`probe_agent.redaction.SENSITIVE_KEYS` and `probe_agent.secret_patterns`) so
the send boundary and the ingestion boundary cannot drift apart. This is not
redundant with the SDK: an older SDK, a non-SDK HTTP client, and
`PROBE_PAYLOAD_MODE=full` all reach this endpoint.

Rules to preserve when touching trace persistence:

- Redact on the write path, never at render time. A presentation-only mask
  leaves plaintext in the DB, in exports, and in Replay / Candidate Studio /
  Workspaces, all of which read the stored row.
- `traces.redaction_json` is `NULL` **only** for rows that predate this
  feature. A clean payload still records `{"redacted": false, ...}`. Do not
  "optimise" the clean case back to `NULL` — `GET /traces/redaction-audit`
  uses exactly that distinction to find rows that were never scanned.
- Redacting `input_capture` degrades `replayability` to `partial` with reason
  `redacted`, one-directionally (never upgrades, never touches
  `unreplayable`).
- Add any new trace column to all four places or the quota accounting silently
  drifts: the `CREATE TABLE`, the `_add_column_if_missing` migration,
  `_stored_trace_bytes`, and `_current_trace_usage`'s SQL sum.
- `POST /traces/redaction-rescan` is an explicit operator action, not a
  startup migration: it destroys data on purpose and the exposed credential
  still has to be rotated by a human. See `docs/secret-redaction.md`.

## Two-axis state endpoints (Issue #366)

Four surfaces in this epic had one displayed word carrying two independent
facts. Each is now two finite axes decided server-side. When touching any of
them, keep the axes separate and keep the decision on the server — the
Dashboard renders, it does not re-derive:

- `GET /connectivity/status` — `state` (cumulative milestone, never regresses)
  and `freshness` (live, regresses). Thresholds come from
  `connectivity_freshness_policy` and are returned with every reading;
  `state_facts.classify_connectivity_freshness` is a pure function of
  `(last_trace_at, now, thresholds)` so boundaries are testable at an exact
  instant. Windowed counts exclude smoke traces.
- `GET /tokens/me`, `GET /tokens` — `app/token_status.py` is the ONLY
  definition of a token's status. Do not add a second one client-side.
- `GET /snapshot-preflight` — processing state vs freshness, one recommended
  snapshot, `unknown` never blocks. `gather_preflight` runs `git`
  subprocesses: never call it inside a `get_conn()` block.
- `GET /replay-readiness` — replayability counts before generation.
  `POST /candidate-sessions` enforces it (422 `no_replayable_traces`) so no
  reasoning-model call is spent on an unevaluable candidate. Keep
  `not_captured` distinct from `unreplayable`.

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

## Shared state-fact retrieval layer (issue #236)

- `app/state_facts.py` is the single home for raw, System-scoped DB reads
  used to derive user-facing state: repository configuration, HEAD / working
  tree state (via `git_ops`), ready/latest snapshot lookup, pipeline step
  rows (`intelligence_runs` / `system_understanding_build_steps` /
  `code_entrypoints` / `understanding_graph_snapshots` / `code_symbols`
  presence), the Purpose/Capabilities base facts
  (`purpose_defined_in_snapshot`, `capability_count_in_snapshot`), build-job
  running/stuck detection, and SDK connectivity counts +
  `classify_connectivity_state`. Every function is a pure `(conn, ...) ->
  value` reader; it never builds a `StateItem` / `PipelineStep` / API
  response and never calls a reasoning model (Principle 6). `system_state.py`
  (`StateItem` construction, `evaluate_understanding` and its baseline/diff
  orchestration), `system_understanding_service.py` (`PipelineStep` /
  `NextAction` / `StageStatus` construction), and
  `routes/connectivity.py` all read facts from here instead of writing their
  own SQL. When adding a new fact these three surfaces would otherwise want
  independently, add the getter to `state_facts.py` first.
- `system_state.evaluate_understanding` (System Purpose / Core Capabilities
  baseline-reuse and diff-impact) stays the canonical orchestrator in
  `system_state.py` per the System State Assessment section below; it now
  calls `state_facts.purpose_defined_in_snapshot` /
  `state_facts.capability_count_in_snapshot` for its per-snapshot base
  facts. `system_understanding_service._build_next_actions` /
  `_derive_stage_statuses` take a `purpose_defined: bool` (not the purpose
  dict) computed once in `get_system_understanding` via
  `_purpose_defined_from_understanding_status(evaluate_understanding(...))`
  -- a reduction of the 5-way `UnderstandingStatus.kind` to a bool where only
  `satisfied_current` is `True` (matching the pre-#236 local formula's
  actual behavior; see `tests/test_state_facts.py`'s
  `TestPurposeDefinedReductionEquivalence` for the equivalence proof and its
  one documented narrow edge case). Do not reintroduce a second local
  purpose-definedness formula in `system_understanding_service.py` --
  compute it via `evaluate_understanding` and the reduction helper.
- Tests: `tests/test_state_facts.py` covers each getter directly plus System
  isolation; regression coverage for the three consuming APIs across
  representative scenarios (unconfigured / snapshot only / pipeline
  complete / connectivity with traces) lives in each API's own existing test
  file (`test_system_state.py`, `test_system_understanding.py`,
  `test_connectivity.py`).

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
- Later phases: Dashboard page callouts/toasts sourced from the same state
  items (not yet implemented), and covering more of the `runtime` /
  `proposal` / `interview` state groups beyond the representative items
  Issue #237 and Issue #238 added. `next_actions` projection is done -- see
  the Issue #238 bullet below.
- **`user_phase` (Issue #237)**: `GET /system-state` also returns
  `user_phase` (`setup | preparation | diagnosis`) and `phases` (each
  phase's completion condition). `system_state.derive_user_phase(facts:
  UserPhaseFacts) -> UserPhaseResult` is a pure, DB-free function --
  `build_system_state` gathers `UserPhaseFacts` from `state_facts` (plus
  two new getters, `count_approved_probe_plans` /
  `count_undecided_completed_experiments`) and from
  `system_diagnostics.run_system_diagnostics`'s checks, filtered to
  categories `repository | database | auth | llm`
  (`SETUP_DIAGNOSTIC_CATEGORIES`) for the setup gate. Current phase = the
  first phase (in `PHASE_ORDER`) whose completion condition is unmet;
  `UserPhaseFacts` defaults are all "not yet satisfied" so an unknown fact
  never advances the phase. Every `StateItem` carries a `phase` field:
  `system_state.STATE_GROUP_PHASE` is the default `state_group -> phase`
  mapping, and `STATE_ID_PHASE_OVERRIDES` is a small explicit per-`state_id`
  exception list (e.g. `runtime.connectivity.no_signal` tags `preparation`,
  not the `runtime` group default, because SDK connectivity is one of the
  two OR'd preparation-completion signals). Phase suppression applies to
  every notification projection -- `primary_item`, `notification_items`,
  and `page_items` all exclude items whose phase is later than the current
  `user_phase` (phase scope is the outermost criterion of the fixed
  priority order); `items` keeps everything for audit. Note that
  `LLM_PROVIDER=mock` pins `intelligence_llm_config` to `blocked` and thus
  `user_phase` to `setup` -- tests that assert later-phase items in
  `page_items` must configure a real reasoning provider via env (see
  `TestUserPhaseIntegration._configure_reasoning_llm`). Two new
  representative items exercise the previously-unused `runtime`/`proposal`
  groups: `runtime.connectivity.no_signal` (preparation-tagged) and
  `proposal.experiments.undecided` (diagnosis-tagged, completed experiments
  with `human_decision = 'undecided'`). Tests: `TestDeriveUserPhase` /
  `TestPhaseTagging` in `tests/test_system_state.py` (pure-function boundary
  cases) plus `state_facts.count_approved_probe_plans` /
  `count_undecided_completed_experiments` coverage (including System
  isolation) in `tests/test_state_facts.py`.
- **`primary_item` absorbs `primary_action` (Issue #238, removal completed
  in #239)**: `select_primary_item` is the canonical "what should the user
  do next" derivation. `system_understanding_service._derive_primary_action`
  / `_build_next_actions` and the `primary_action` / `next_actions` /
  `understanding_refresh_recommended` fields on `GET
  /repository/system-understanding` were removed in Issue #239 (the
  Dashboard consumption switch happened in the same commit;
  `_check_understanding_refresh_recommended` survives only as the source of
  the `interview.materialized.rebuild_required` state item). Do not
  reintroduce these fields; read `primary_item` / `page_items` from
  `GET /system-state` instead. Two new
  native `state_group="pipeline"` items close a gap the pipeline-step
  factors weren't fully covered by: `pipeline.docs_code_reconcile.not_run` /
  `.partial` (mirrors `system_understanding_service._check_docs_code_reconciled`'s
  "has an understanding graph AND has code symbols" condition -- the
  pre-existing `diagnostic.pipeline_understanding_graph` diagnostic check
  only tests graph presence, so it already covers
  `documentation_claims_scanned` but not the code-symbols half of
  `docs_code_reconciled`, hence no separate native item for the former).
  Two new `state_group="proposal"` items close the probe-plan-review gap:
  `proposal.probe_plans.proposed` (count of `status = 'proposed'` plans,
  `phase="preparation"` override -- reviewing/approving one is how a user
  reaches the approved-plan half of `derive_user_phase`'s instrumentation-path
  OR condition, same rationale as `runtime.connectivity.no_signal`'s
  override) and `proposal.probe_plans.approved_without_patch` (count of
  approved plans whose latest patch has not passed both `baseline` and
  `probed` validation, default `phase="diagnosis"` since an approved plan
  already satisfies that OR condition regardless of patch status). The
  "has this plan's patch been validated" check moved from
  `system_understanding_service._plan_has_validated_patch` to
  `state_facts.plan_has_validated_patch` (plus new
  `state_facts.count_proposed_probe_plans` /
  `count_approved_probe_plans_without_validated_patch`) so both surfaces
  share one query. No `StateItem` was added for the terminal "everything
  satisfied, explore from here" `next_actions` fallback (`Start from
  Capability` / `Start from Feature` / `Open Flow Explorer`):
  `select_primary_item` only ever selects `severity != "ok"` items, so a
  fully-satisfied system correctly yields `primary_item = None` there
  instead of a decorative nudge -- an intentional divergence from the old
  field's behavior, not a gap. A second intentional divergence: the old
  `_derive_primary_action` rule 2 unconditionally blanks `primary_action`
  while any build is queued/running, regardless of cause; the new model has
  no equivalent blanket rule -- only the pipeline step(s) an active build is
  actually processing become `user_action_kind="wait"` (excluded from
  `select_primary_item` candidacy), so an unrelated outstanding item (e.g. a
  probe plan awaiting review) is not suppressed just because a System
  Understanding build happens to be running concurrently. Contract tests
  pinning old/new agreement for the representative cases where they are
  expected to agree (repository unconfigured, snapshot not ready, a single
  incomplete pipeline step, purpose undefined, proposed/approved-without-patch
  probe plans, a genuinely idle active build) plus both intentional
  divergences and the `understanding_refresh_recommended` ==
  `interview.materialized.rebuild_required`-presence equivalence live in
  `tests/test_next_step_parity.py`.
- **Message catalog (Issue #240)**: all user-facing state copy (summary /
  detail / impact / remediation / action_label, pipeline-step / stage
  display names, gap titles / next-actions, the Hub success summary) lives
  in one server-side catalog, `app/state_messages.py`, and the display
  language is Japanese. `system_state.py`, `system_diagnostics.py`, and
  `system_understanding_service.py` look copy up from the catalog by
  `state_id` / `check_id` (+ variant) instead of holding f-strings.
  Accessors (`state_message`, `pipeline_family_message`,
  `understanding_message`, `check_title`, `check_message`,
  `shared_check_message`, `stage_message`, `pipeline_step_detail`,
  `gap_title`, `gap_note`, `pipeline_not_run_remediation`, `success_summary`)
  raise `KeyError` on a missing key -- never a silent English/blank fallback
  (`phase_label` is the one deliberate exception: it returns the raw token
  for a server-validated enum). **When you add a new `StateItem` /
  `DiagnosticCheck` / pipeline step / stage, add its catalog key in the same
  change** -- `tests/test_state_messages.py` fails otherwise (it verifies
  every `ALL_*` key resolves, drives the real `run_system_diagnostics` /
  `build_system_state` producers asserting every emitted id resolves to
  Japanese, and snapshots representative strings). Dynamic content stays
  limited to finite facts (counts, snapshot ids, raw upstream status/error)
  interpolated as named `str.format` params; no reasoning model authors
  copy. Dashboard consumes server copy (stage `label`/`description`,
  `SystemUnderstandingOut.success_summary`, `user_phase` labels, gap
  actions) and keeps its local label maps only as a last-resort fallback.

## Manual System Profile alignment (issue #275, formerly #94)

- `GET /repository/system-understanding` returns `purpose_views` (parallel
  provenance views) alongside the unchanged legacy `purpose` field: the
  manual `system_profile` view (`source: system_profile`,
  `provenance_kind: manual`) is snapshot-independent and present whenever
  the profile's purpose is non-empty; the AI/source view follows
  `_load_purpose`'s existing hierarchy-node → draft order and appears only
  with a ready snapshot. Do not change the legacy `purpose` fallback chain.
- `POST /repository/system-understanding/purpose-confirmation` records the
  human's cross-check as an append-only `system_purpose_confirmations` row
  (`decision_method: manual`, both sides captured verbatim, System-scoped).
  409 on missing/mismatched snapshot, 422 when either side is absent.
  Never UPDATE/DELETE confirmation rows.
- Staleness of the latest confirmation is read-time structural equality
  only (`snapshot_changed` → `profile_updated` → `ai_updated`); no
  similarity scoring or heuristic match/mismatch judgement (Principle 6) —
  interpreting the difference is the human's job.
- The unconfirmed pairing surfaces as StateItem
  `understanding.purpose.manual_profile_unconfirmed` (info,
  `user_action_kind: confirm`, anchor `purpose-views`, display route
  `/system-understanding`); it disappears once a valid confirmation
  exists. Copy lives in `state_messages.py`.
- Shared readers (profile row, AI purpose view, latest confirmation,
  staleness) live in `state_facts.py` and are consumed by both
  `system_understanding_service` and `system_state` — do not duplicate
  the queries.

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
- **`GET /auth/bootstrap-status` (Issue #265)**: the one endpoint reachable
  with no credentials and no System (`app/bootstrap_status.py`,
  `routes/auth.py`) -- everything else (`GET /system-state`,
  `system_diagnostics`, and every Dashboard component built on them) needs
  `X-Probe-System-Id`, so a pre-login / zero-System install had no
  deterministic state to show. Returns exactly four finite facts:
  `admin_exists` (a System-id-free `role='admin' AND is_active=1` check,
  independent from `system_diagnostics._check_auth_scope`'s system-scoped
  "any active user" check), `auth_mode` (`"anonymous" | "user"`, mirrors
  `auth.auth_enabled()`), `llm_configured` (env-presence only, never
  validates the key -- mirrors `_api_key_status`'s presence logic without
  reusing that private helper), and `environment`
  (`environment.control_env()`). Never a username, key value, path, or
  hostname. `llm.KNOWN_PROVIDERS` is the single source of truth for the
  provider finite set; `system_diagnostics.py` imports it rather than
  duplicating it.

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

## Replay / Simulation (issue #242)

- Modules: `app/replay_harness.py` (the standalone worktree harness SCRIPT +
  `write_harness_files` / `run_inline_candidate`; `REPLAY_HARNESS_VERSION`),
  `app/replay_runner.py` (worktree lifecycle, `_run_command` sandbox reuse,
  deterministic input restoration + baseline-vs-recorded comparison),
  `app/replay_variants.py` (baseline-replay-vs-candidate-replay finite
  classification), `app/replay_draft.py` (LLM candidate draft →
  deterministic git-diff), `app/comparison.py` (the shared `field_equal` /
  `value_equal` / `diff_fields` extracted from `trace_analyzer.py`; #150's
  `test_shadow_diff.py` must stay green). Routes in `app/routes/replay.py`.
- Tables (System-scoped, cascade FKs, additive CREATE-only): `replay_sets`,
  `replay_runs`, `replay_case_results` (#244), `replay_variants`,
  `replay_variant_case_results`, `replay_variant_drafts` (#245), plus
  `replay_approvals` (the approval gate) and
  `replay_regression_scaffolds` (review-only #246 reasoning drafts).
  `traces` gained additive
  `input_capture_json` / `replayability` / `replay_reasons_json` columns
  (#243).
- The replay approval gate is a human `decision_method: manual` record;
  `POST /replay-runs` / `POST /replay-variant-runs` return 403 without an
  active (non-revoked) approval. Risk context shown at approval time reuses
  persisted probe-plan `side_effect_risk` / `replayability` labels (display
  only — no new reasoning run) plus the fixed Principle-4 warning.
- Replay routes are management-plane APIs and require a user session. SDK API
  tokens remain data-plane only and must not read source, create Replay Sets,
  draft patches, or trigger replay execution.
- Execution goes through `validation_runner._run_command` so network-off /
  env-allowlist / no-sandbox fail-closed are inherited unchanged;
  `PROBE_ENABLED=false` + `PYTHONHASHSEED=0` are injected; worktrees are
  always cleaned up. Comparison and classification are finite sets only
  (Principle 6); LLM candidate drafts / interpretations are `reasoning_llm`,
  fail-closed, `is_mock` surfaced, with raw deterministic results kept
  separate (drafts store provenance via an `intelligence_runs` row).
- The standalone harness resolves a symbol using its real package-qualified
  module name when the snapshot path is inside a Python package, so ordinary
  relative imports keep working inside the isolated pinned worktree.
- Recorded-error traces ARE executed against candidates on this offline side
  (`error_to_success` etc.); the live SDK shadow asymmetry is unchanged.
- Phase D adds only two DETERMINISTIC endpoints (no judgement):
  `GET /replay-sets/{id}/source` and `POST /replay-source-diff` — both read
  the pinned snapshot only (Principle 5), never the working tree.
- `POST /replay-regression-scaffolds` is the separate `reasoning_llm`
  boundary: it accepts only a completed/applied variant case, persists both
  success and failure provenance in `intelligence_runs`, stores review-only
  generated text separately, and never writes to the target repository.
- New env vars: `PROBE_REPLAY_WORKSPACE_BASE`, `PROBE_REPLAY_TIMEOUT_SECONDS`
  (server); `PROBE_REPLAY_CAPTURE_MAX_BYTES` (SDK). See the Issue #242
  section of `docs/project-intelligence.md`.

## Reviewable policy artifacts (issues #313, #341)

Deterministic classification rules that operators are expected to tune live in
`app/policies/*.yaml`, not in Python literals, and are loaded through
`app/policy_loader.py`'s strict helpers (`UniqueKeySafeLoader`,
`require_mapping` / `require_exact_keys` / `require_nonempty_string` /
`require_enum` / `parse_enum_list`, each taking the caller's own error class).

- `alignment_review.yaml` — the no-review-required first-match rule table.
- `interview_metric_attention.yaml` — the Interview UX metric 要確認 criteria.

Rules for any new artifact:

- Load once at import so a broken policy fails startup, never the first
  request, and NEVER fall back to an embedded default (Principle 6).
- Validate schema version, exact key sets, every finite value, duplicate keys,
  and terminal coverage of all inputs at load time.
- Persist or return the `policy_version` and the SHA-256 of the raw bytes, so a
  changed policy is auditable and invalidates anything cached under the old one.
- Keep the vocabulary limited to what the evaluator actually honours. The
  attention policy therefore accepts only `window: all_time`,
  `trigger: single_breach`, and `clear: value_within_threshold`: sustained
  triggers, bounded windows, and manual 「確認済み」 acknowledgement all need
  persisted evaluation history or a scheduled evaluator, and silently ignoring
  an unsupported value would be worse than rejecting it.
- `guardrail` on `InterviewMetricOut` is only the DESIGNATION of a metric worth
  watching; the evaluated judgement is the separate `attention` object. A
  policy may only set `watch: true` on a designated guardrail — `apply_attention`
  raises otherwise. Missing values never become `ok` or `attention`: they split
  into `insufficient_data` (denominator empty / sample below minimum) and
  `not_measurable` (the underlying fact is not recorded at all).

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
- NEVER hold a `get_conn()` connection across an external call. `db.py`'s lock
  is process-wide and NON-REENTRANT, and the server runs a single uvicorn
  worker, so a connection held across an LLM round trip stalls every other
  request for its duration. Worse, every LLM client consumes System quota via
  `resource_limits.consume_llm_execution()`, which opens its OWN connection:
  calling an LLM inside `with get_conn()` self-deadlocks the whole process
  permanently (no timeout breaks it; only a restart does). `get_conn()` now
  raises `DatabaseReentrancyError` instead of hanging, but the fix is to
  structure the endpoint in 3 phases: read under the lock, close it, run the
  reasoning call, then reopen to persist. Canonical examples:
  `routes/interview.py::run_runtime_reality_check` and
  `cell_orchestrator.run_triage`. Helpers that need both DB and an LLM
  (`_rebuild_understanding`, `run_alignment_build`, `_generate_and_store_answer`,
  `investigate`) own their connections rather than receiving one, and their
  docstrings say so — call them with none open.
  Mock providers do NOT exercise this: reasoning entry points fail closed
  before `generate_text` on a mock/non-reasoning client, so any test that
  must reach the LLM path has to configure a real reasoning model
  (`openai`/`o3`) and stub `llm._request_json`.
- The System Interview's developer-facing state is decided in exactly ONE
  place: `app/interview_workflow.py` (Issue #349, implementing
  `docs/system-interview-workflow-ux.md`). `evaluate_candidate_state` is the
  13-row first-match table; `apply_backward_hold` is the ordered-state
  (`W2 < W3 < W4 < W5 < W6 < W7`) backward hold. Both are pure functions of a
  `WorkflowFacts` value — no clock, no request-scoped value, no client state
  — so the same persisted facts always yield the same state. Add a new input
  by adding a field to `WorkflowFacts` and reading it in `gather_facts`;
  never branch on something a reload would lose.
  - Any process that should show 「システムが調べている」 (`W1`) must persist a
    run record via `process_run` / `ProcessRunTracker` / `tracked_process`.
    They open short-lived connections only, so they are safe around an LLM
    call — but the tracked function must still own its own connections.
  - A 404/409 precondition rejection is `abandon()` (nothing ran), not a
    failure. Recording it would surface a retry the developer cannot succeed
    at. A deliberate skip (the Runtime Reality Check's finite skip condition)
    is likewise not a run.
  - Failure classification is deterministic and lives in `classify_failure`:
    it depends on the material left behind (`E3-a` vs `E3-b`, `E4-a` vs
    `E4-b`), not on which process failed. `target_state` is the finite set
    `W2` / `W4` / `W5`; nothing else may be blocked.
  - "Unresolved" is derived (no later success of the same `process_kind`),
    never stored. `OP-D14` suspend/resume therefore cannot turn a failure
    into a solved one — it only flips the existing session `status` and adds
    an `interview_session_status_audit` row.
  - `W0-B` completes by "session created AND the system started
    investigating", so `POST /interview/sessions` opens the initial build's
    run record itself AND dispatches the build. Leaving either half to a
    second client call is a bug: without the record, a reload in between
    produces a session that falls through the whole rule table to `W7` — a
    terminal with no way back, because every build control is exception-only;
    without the dispatch, the record describes a process nobody is running,
    and an API-only or closed-tab session shows 「システムが調べている」
    forever. The worker adopts that exact record
    (`ProcessRunTracker.start(adopt_run_id=...)`), a dispatch that cannot
    start is failed on the spot as a recoverable `E3-a`, and
    `PROBE_INTERVIEW_EAGER_INITIAL_BUILD=1` makes it synchronous for tests.
    Because the server starts the build, the client must NOT also post
    `update-understanding` — that runs the same reasoning build twice.
  - `adopt_run_id` names ONE record. "The oldest running row of this kind"
    lets two legitimately overlapping rebuilds (a manual update while the
    automatic refresh is in flight) share a row, so whichever finishes last
    decides for both and can erase the other's failure.
  - Resolution of a failure reads the LATEST finished run per `process_kind`
    (`finished_at`, id as tiebreak), not "any later success": two runs of a
    kind can overlap and the one that started first can finish last, and with
    three or more an older success would mask the newest failure.
  - `tests/conftest.py` stubs `_dispatch_initial_understanding_build` for
    every test — otherwise every test that creates a session starts an
    unbounded reasoning call on a daemon thread, racing its own writes.
    `test_interview_workflow.py`'s `real_initial_build_dispatch` fixture puts
    the real one back for the two tests that assert on the dispatch.
  - The stale sweep is a GUESS from elapsed time (nothing writes
    `heartbeat_at` while a process runs), so `finish_process_run` still
    accepts a swept run and lets its real outcome replace the guess.
  - A suspended session (rule row 3) never auto-resolves a pending backward
    request: `W7` there means "the developer left", not "the backward
    question was settled", and §5.4 requires resuming to acknowledge it.
  - `open_required_questions` must exclude questions held by an in-flight
    handoff. Handing off deliberately leaves `interview_qa.status` alone, so
    the status column alone cannot express 「引き継ぎ済み」 (spec §2.3 `W3`).
  - `routes/interview_workflow.py` writes exactly two developer decisions
    (the diff review and the backward acknowledgement, both
    `decision_method: manual`) and two system-recorded progress facts (the
    `reached_state` checkpoint and the backward request). It confirms no
    understanding, settles no Alignment item, approves no proposal, applies
    no diff, and starts no observation.
- The Understanding Brief and Decision Readiness (Issue #351) are derived in
  exactly ONE place too: `app/understanding_brief.py`, served read-only by
  `GET /interview/understanding-brief`. It is a second axis over the same
  persisted facts, not a second workflow state.
  - `classify_confirmation` (確認状態) and `classify_provenance` (出所) are
    independent finite classifiers and must stay independent: provenance
    answers "who wrote this claim", confirmation answers "is it settled".
    Never let a human confirmation change a claim's provenance.
  - `evaluate_readiness` is first-match over a `ReadinessFacts` value, same
    purity rules as `WorkflowFacts` (no clock, no request-scoped value, no
    client state) except for the runtime-freshness input, which the DB layer
    resolves before calling it.
  - Readiness NEVER gates. The single primary action keeps coming from
    `app/interview_workflow.py` and must stay reachable under `blocked` —
    "stop the flow until every uncertainty is gone" is an explicit non-goal.
    The endpoint writes nothing.
  - Only `BRIEF_AFFECTING_PROCESS_KINDS` may move the verdict — both for
    running records and for blocking failures. A running `proposal_generation`
    is not 「理解を作成しています」, and a failed `diff_generation` (always a
    blocking failure) is not 「理解を作る処理が失敗した」. Add a kind to that
    tuple only if it really writes `current_understanding` or an Intent Brief
    item.
  - Claim identity is exact name equality and content change is a
    `claim_digest` comparison (name excluded). Do not introduce similarity
    matching here — a rename is a remove + an add, exactly as in
    `understanding_diff`.
  - The Brief's finite vocabularies (`UnderstandingConfirmationState`,
    `UnderstandingProvenanceKind`, `UnderstandingClaimKind`,
    `UnderstandingReadinessState`, `UnderstandingReadinessSeverity`,
    `UnderstandingChangeKind`) are declared ONCE as `Literal` aliases in
    `app/models.py`; `understanding_brief.py` derives its tuples with
    `get_args`, and `tests/test_interview_type_parity.py` holds the Dashboard
    unions to the same sets. A bare `str` field here means the response schema
    carries no enum and the TypeScript union can drift unnoticed — which is
    exactly what happened when `change_kind` gained five members.
  - `claim_payload` is the single definition of a claim's content: the digest
    that decides a recheck AND the change list the developer reads are both
    built from it. Adding a field to the payload means adding it to
    `_DETAIL_FIELD_CHANGES` too, or a claim becomes reportable as "changed"
    with nothing to show. `understanding_diff` covers names, summaries and
    confidence only — never assume it covers the rest.
  - The confirmed baseline is #312's
    `understanding_capability_confirmation.source_revision_id`, with a
    fallback to the newest revision at or before `understanding_confirmed_at`
    for zero-base and pre-#312 sessions. Both are persisted facts; never
    guess which revision "was probably on screen".
  - Adding a `current_understanding` section (as #352 did with `vision`)
    means updating `system_understanding_reviewer` (field + prompt +
    `PROMPT_VERSION`/`SCHEMA_VERSION`), `understanding_diff.
    UNDERSTANDING_SECTIONS`, `interview_workflow._has_understanding_content`,
    and the Dashboard's `CurrentUnderstanding` / `hasUnderstandingContent` —
    all five, or the section silently stops counting as content somewhere.
- Additive nullable columns go through `db.py`'s `_add_column_if_missing`
  (Issue #308). It only ever adds a nullable column and never backfills, so
  do not use it for NOT NULL/DEFAULT migrations that need a value written
  into existing rows. An index over a newly added column belongs in the
  migration block, not in `SCHEMA`: that script also runs against older
  databases, where `CREATE TABLE IF NOT EXISTS` is a no-op and the indexed
  column does not exist yet.

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
- no `get_conn()` connection is held across an LLM call — `tests/test_db_lock_isolation.py`
  keeps a static check over every `with get_conn()` block plus dynamic checks
  with a real reasoning model configured; extend it rather than duplicating it
- committed-only snapshot behavior
- reasoning-required operations fail closed without heuristic fallback
- reasoning metadata and structured-output validation
- target repository unchanged after workspace operations
- GitHub publish workflow: no installation token/JWT/private key ever
  persisted or returned, `probe/`-branch + no-force-push enforcement, the
  Issue #25 validation gate at job creation, the pre-push staleness
  re-check, approve/cancel idempotency, worktree cleanup on every terminal
  state, and system isolation

## Probe Cell Fabric -- Goal/Task ledger (issue #300)

Sub 3 of the Probe Cell Fabric epic (Issue #297; Sub 1's contract layer is
`app/cell_fabric.py`, Issue #298). See the "Probe Cell Fabric(Issue #297)"
section of `docs/project-intelligence.md` for the full epic design.

- Core logic in `app/cell_tasks.py`; routes are thin
  (`routes/cell_tasks.py`) and only translate `app.cell_tasks` errors to
  HTTP status codes: `NotFoundError` -> 404, `ConflictError` -> 409,
  `ValidationFailedError` -> 422.
- Tables (System-scoped, additive `CREATE TABLE IF NOT EXISTS`, cascade
  FKs): `cell_goals` (parent_goal_id nullable = root goal), `cell_tasks`
  (exactly one `owner_cell_id` + one `goal_id` per row -- no many-to-many
  membership table at this Sub), `cell_task_events` (append-only transition
  audit), `cell_reports` (`kind` = `digest` | `escalation` only),
  `cell_escalations` (created automatically from an escalation-kind report
  in the same transaction).
- Task transitions delegate legality to `cell_fabric.TASK_TRANSITIONS` /
  `validate_task_transition` (Issue #298) -- this module never re-implements
  the transition table. It only adds ledger-specific rules on top: a retry
  (`failed -> todo`) increments `retry_count` and is refused (409) once
  `retry_count >= retry_limit`; entering `blocked` requires `blocked_by`
  task ids or an explicit `detail`; every transition writes exactly one
  `cell_task_events` row (event_type is `created` / `transition` / `retry`
  / `blocked` / `unblocked` / `returned_to_parent`, chosen structurally from
  the from/to status pair, never inferred after the fact).
  `return_to_parent` is only legal from `failed` or `blocked`.
- Delegation (P1, `delegate_task`) reuses `cell_fabric.TaskDelegation` for
  the acceptance/context_refs/budget/deadline/priority contract instead of
  re-validating it -- a missing/empty `acceptance` list is the same
  fail-closed error as #298's contract layer.
- Evidence/context ref resolution (`resolve_evidence_ref`, Principle 6:
  deterministic, finite ref grammar only) accepts exactly `trace:<id>` /
  `evaluation:<id>` / `shadow_result:<id>` / `replay_run:<id>` /
  `experiment:<id>` / `snapshot_file:<snapshot_id>:<path>`, and verifies the
  referenced row exists AND belongs to the calling System (snapshot_file
  additionally checks the path exists in `snapshot_files` for that
  snapshot). Applied at the `done` transition's `evidence_refs`, a task's
  `context_refs`, and a report's per-fact `evidence_refs`. `quality_sample:<id>`
  and `improvement:<id>` are RESERVED P3/P4 ref formats (Sub 6 / #302 and
  Sub 7 / #304 respectively) -- they parse but always fail closed with a
  "not yet implemented" message; do not make them resolvable here.
- Reports (P2, `submit_report`): `kind` outside `digest`/`escalation` is
  rejected fail-closed, and any unknown request field is rejected by the
  Pydantic `extra="forbid"` request model. `escalation` requires
  `severity`; `digest` must not set one. `fact` / `interpretation` / `ask`
  are stored as separate JSON columns -- raw evidence-backed facts are
  never mixed with interpretation/ask text (Principle 7 discipline, even
  though this module calls no reasoning model).
- Idempotency: `delegate_task` and `submit_report` both accept an optional
  `idempotency_key`; a resend with the same `(system_id, idempotency_key)`
  returns the EXISTING row unchanged (`UNIQUE (system_id, idempotency_key)`
  -- SQLite treats distinct NULLs as non-conflicting, so tasks/reports
  without a key never collide with each other).
- Goal cycle rejection (`would_create_cycle`) walks the parent chain
  deterministically. There is no reparent endpoint at Sub 3 (only goal
  creation and a status-only update), so the checker is exercised directly
  in tests the same way #298 tests `validate_task_transition` directly --
  it exists so a future reparent path can reuse it without adding a new
  cycle-detection implementation.
- This module has NO reasoning-model call anywhere (non-goal for #300):
  orchestrator aggregation/triage is #301, quality sampling is #302,
  improvement proposals are #304.
- Tests: `tests/test_cell_tasks.py`.

## Probe Cell Fabric -- epic-wide map (issue #297, subs #298-#304)

See the "Probe Cell Fabric(Issue #297)" section of
`docs/project-intelligence.md` for the binding design. Module map:

- `app/cell_fabric.py` (#298): shared-schema mirror models
  (`extra="forbid"` fail-closed), `TASK_TRANSITIONS` /
  `validate_task_transition` (done requires acceptance + evidence),
  Role Card semver compat (same-major AND >= pinned), and
  `resolve_model_alias` (`CELL_MODEL_ALIAS_<UPPER_ALIAS>` env,
  `provider:model`; unset falls back to `intelligence_from_env`). Role
  Cards never store literal provider/model names; changing the env value
  never requires a card revision. Tables `agent_role_cards` (append-only
  versions) / `cell_definitions` (roster_json NULL = worker, non-null =
  orchestrator; no separate kind column).
- `app/cell_binding.py` (#299): `cell_bindings` append-only versions from
  APPROVED probe points / pattern points only; drift is purely structural
  (`active`/`stale`/`review_required`, never re-binds); `build_cell_health`
  aggregates traces/activations deterministically (unobserved = None).
  `cell_activations` audits explicit/aggregation-window triggers; there is
  no per-trace LLM path anywhere in the fabric.
- `app/cell_tasks.py` (#300): see the dedicated section above.
- `app/cell_orchestrator.py` (#301): roster guardrails (span <= 7,
  depth <= 3, no self/cycle, members must exist; static rosters changed
  only via `PUT .../roster` + `cell_roster_events` audit); deterministic
  digest with finite bottleneck rules (queue_depth / stuck_task /
  blocked_chain / retry_churn, facts attached); `run_triage` is the ONE
  reasoning boundary (run_type `cell_triage`, fail-closed, facts survive
  failure, roster-external affected ids rejected).
- `app/cell_quality.py` (#302): quality sampling is a SEPARATE contract
  from the SDK's lineage `sample_rate`. Deterministic stable-hash
  stratified selection (rare strata guaranteed >= 1); audit VERDICT is
  deterministic via `evaluator.py` (`pass`/`fail`/`no_criteria`), only the
  failure explanation is reasoning_llm (fail-closed; verdict row survives
  LLM failure); blind re-audits never read prior audit rows; quality-floor
  breach suspends ONLY that cell's intake + sev1 escalation via
  `cell_tasks.submit_report`; `cell_quality_usage` enforces the
  System-scoped daily audit budget.
- `app/cell_root.py` (#303): `GET /cell-fabric/root-digest` reuses
  `system_state.build_system_state` as the canonical fact source (call it
  BEFORE opening your own `get_conn` -- it manages its own connection);
  4-level progressive disclosure (conclusion / key_points / evidence /
  audit), sev1 -> conclusion, sev2 -> key_points, sev3 -> evidence,
  sha256 dedupe with merged `sources`. `cell_asks` decisions are
  `decision_method: manual`; `execution_approved` is ALWAYS 0 here --
  proposal accept never executes anything. Dashboard page:
  `apps/dashboard/src/pages/cell-fabric.tsx` (Japanese UI).
- `app/cell_improvement.py` (#304): finite lifecycle `observed ->
  proposed -> canary_ready -> canary_running -> adopted|rejected|blocked`
  with append-only `cell_improvement_events` (rejected history never
  deleted); canary evidence refs restricted to existing Replay /
  Experiment / Evaluation rows (no new execution path); `adopted`
  requires BOTH parent and human approval; role_card adoption re-pins
  `cell_definitions.role_card_id` (rollback re-pins back);
  candidate_patch adoption is a handoff marker only -- the real
  adoption/publish flows through the existing #25/#216/#242/#252 gates.
  `cell_shadow_decisions` keeps `shadow_proposal` and
  `live_shadow_execution_approval` as separate manual records; approving
  execution writes NO policy/candidate rows. Rubric changes are
  parent-owned; >= 3 consecutive rejections auto-suspend the cell's
  improvement rights.

Cross-cutting rules for every fabric module:

- `db.get_conn()`'s lock is NON-REENTRANT: pass `conn` into helpers, and
  NEVER hold a connection across `generate_text` (the quota wrapper opens
  its own connection) -- use the 3-phase read / LLM / write structure of
  `cell_orchestrator.run_triage`. This is now a server-wide rule (see
  `## Rules`), enforced by `get_conn()` itself and by
  `tests/test_db_lock_isolation.py`.
- All tables are System-scoped with isolation tests; all reasoning goes
  through `intelligence_runs` fail-closed with `is_mock` surfaced; the
  Probe SDK is never touched by fabric work.
- Tests: `tests/test_cell_fabric.py`, `test_cell_binding.py`,
  `test_cell_tasks.py`, `test_cell_orchestrator.py`,
  `test_cell_quality.py`, `test_cell_root.py`,
  `test_cell_improvement.py`.
