---
description: Use when implementing or modifying the dashboard for systems, repository intelligence, Feature Maps, Probe Plans, experiments, traces, policies, and comparisons.
---

# Dashboard Skill

## Scope

Use this skill for files under:

- `apps/dashboard/`

## MVP Requirements

The dashboard should support:

- component list
- trace list by component
- input / output / error / duration display
- policy mode display
- policy mode update
- shadow comparison display
- manual evaluation: better / worse / same / unsure
- login/logout with username/password (`/auth/login`, `/auth/logout`)
- self-service API token management (My Tokens)
- admin-only user management tab
- Repository tab
- Feature Map tab
- Probe Planner tab
- Probe Patterns tab (Issue #168, `pages/probe-patterns.tsx`): pattern
  list/detail with status (`active`/`stale`/`archived`/`superseded`),
  objective, point counts, source commit, lifecycle history, and the latest
  reconciliation summary. The pre-release flow scans the latest snapshot's
  `@probe` instrumentation, saves selected probes as a pattern (inheriting
  probe-plan context), then generates a removal diff that is applied only
  after a typed confirmation (REMOVE) against the pinned commit. The
  re-development flow reconciles a pattern against the latest snapshot and
  renders each point's classification badge, decision method, evidence, and
  hypothesis with a short confirm question — はい (accept) / いいえ
  (reject) / わからないので調べる (investigate; renders the returned
  implementation-state summary + recommendation). Exact matches need no
  decision; `unsafe`/`missing` can never be accepted. "Create Probe Plan"
  hands off to the existing Probe Planner gates and links there — the page
  itself never applies instrumentation.
- Experiments tab
- Decision Workspace tab (Issue #38): workspace list/create/switch, a
  conversation thread with grounded findings/assumptions/missing information
  visually distinguished, pinned context with links back to the owning
  Feature Map/Components/Probe Planner/Experiments tab, and proposal
  accept/reject with a required reason. There is no "defer" decision or
  proposal-edit action in the API (Issue #35 only exposes accept/reject); do
  not add UI controls for actions the API does not support.
- System settings diagnostics (Issue #101): a header alert badge
  (`components/diagnostics-badge.tsx`) fed by `GET /system-diagnostics`.
  The badge count is error+blocked+warning checks; clicking opens a dialog
  showing each check's detail, impact, remediation, related env vars, and
  the verbatim last observed run error. The System Understanding page shows
  a "Why?" button on missing/blocked pipeline rows that expands the related
  diagnostics. Diagnostics are deterministic server output — never decorate
  them with client-side heuristic explanations.
  Issue #115: the dialog text is Japanese and each problem is clickable.
  A `fix_kind: navigate` check routes to `fix_page?diagnostic=<id>&fix=<anchor>`
  and closes the dialog; a `fix_kind: dialog` check opens an env-var
  remediation dialog (which env vars, plus restart/re-run steps). Target
  pages render a `diag-anchor` on the fix control and an inline
  `DiagnosticFixCallout` (`components/diagnostic-fix.tsx`) that highlights the
  control and shows 原因 / 次の操作 verbatim from the diagnostic — no
  client-side interpretation.
- Per-screen assistant (Issue #102): a floating agent button rendered by the
  app layout on every page (`components/assistant-panel.tsx`). It opens a
  right-side panel showing the screen's purpose, the current diagnostics
  state for that screen, suggested questions (failing checks first), a
  free-text question box, and answers from `POST /assistant/ask`. Answers
  must render `used_fallback` and decision method visibly, list citations
  (settings, diagnostics checks, pipeline steps) and suggested actions;
  navigate actions use client-side routing. The screen id sent to the API is
  the route's first path segment (`/` → `overview`). The panel must not
  block or overlap primary page actions when closed.
- System Understanding build job polling (Issue #109): Build / Refresh
  triggers an async job and the page polls `/repository/system-understanding/
  build/latest` (2s while queued/running). The job panel
  (`pages/system-understanding.tsx` `BuildJobPanel`) must show per-step
  status/duration/error, claim-scan chunk progress, artifact counts, a
  `stuck` badge when `is_stuck`, and cancel (job/step) plus retry (job/step)
  actions. Never block the mutation waiting for completion, never offer
  retry on a completed step, and rely on the server-persisted job so a
  browser reload restores the active/last job state.
- System Interview auto-understanding-first flow (Issue #123): the interview
  page (`pages/interview.tsx`) is Japanese-language and state-driven. Clicking
  「インタビューを開始」 creates the session and immediately triggers
  `update-understanding` (auto-understanding-first). The UI derives one
  explicit state from server data (deterministic finite set):
  `preparing` (analysis running) / `needs_build` (no understanding yet, no
  error) / `confirm_understanding` (inferred summary shown for confirmation)
  / `fill_gaps` (one focused question at a time) / `zero_base` (fallback
  interview when understanding failed or is empty) / `ready_for_proposals` /
  `proposal_review`. A next-action banner always states the required user
  action. There is no manual stage-advance control — the server advances the
  stage on each dialogue turn. `Build Understanding` is kept only as the
  secondary 「理解を更新」(refresh) action. Proposal and diff panels stay
  hidden until the `proposal_generation` stage AND the proposal gate is
  unlocked. The gate (server-side, Issue #83 + #123) passes when a built
  `current_understanding` exists OR the developer explicitly confirmed the
  zero-base context via `POST .../confirm-understanding` (persisted as
  `understanding_confirmed_at/by` — a manual decision record). The UI never
  shows "ready for proposals" while the gate is locked; in that case it
  shows the 「この内容で提案生成に進む」 confirm action instead.
  Zero-base questions are a fixed UI questionnaire (goal / affected area /
  desired change / constraints / success criteria); answers still flow
  through the reasoning-model dialogue turn — never heuristic inference.
  Each dialogue turn sends `answered_qa_id` (the focused open question's
  `qa_id`, Issue #129) plus `answered_question` (exact text, kept for
  sessions predating the Q&A layer) so the server consumes the question
  from `open_questions` AND marks the matching `interview_qa` row answered;
  the UI must not re-ask answered questions.
- Hypothesis-first questions (Issues #127/#128): all LLM-generated interview
  text is in the configured `INTERVIEW_LANGUAGE` (default Japanese; JSON
  keys/enums stay English). Open questions may carry `hypothesis`,
  `evidence_refs` (path + line range, server-validated against the pinned
  snapshot), and `answer_options`. The focused-question card renders the
  hypothesis and evidence, and shows quick answers: 「はい、正しいです」
  sends a canned confirmation through the normal dialogue turn;
  「いいえ(修正を入力)」only prefills the textarea with a correction
  prefix and focuses it (no API call); `answer_options` render as
  send-on-click buttons. Quick answers are plain dialogue input — they are
  NOT approval actions; the proposal approval gate is unchanged.
- Empty-proposal narrowing: a `generate_proposals` turn can legitimately
  return zero proposals when the reasoning model lacks grounded targets; the
  server then returns narrowing questions and `proposals_requested: true` in
  the dialogue-turn response. In `ready_for_proposals`, if the session still
  has open questions, the focused-question card shows the top one (with
  hypothesis / answer options / 「わからない」) instead of the fixed
  "ready" prompt, consumes it via `answered_qa_id` on send, and each answer
  re-requests proposal generation. When a requested turn yields no proposals
  and no error, show an informational toast (narrowing continues) — never a
  bare success toast that implies proposals were created.
- Cross-page onboarding and navigation (Issue #212): Overview's zero-component
  state shows a deterministic, ordered get-started list (Repository →
  System Understanding → Connect SDK), optionally preceded by
  `useSystemState()`'s `page_items["/"]` primary item via the canonical
  `SystemStateBanner` when the server ever routes an item there. Probe
  Planner's Feature field falls back to a prerequisite note (links to
  `/feature-map` and `/system-understanding`) plus an explicit
  "Enter feature id manually (advanced)" toggle when no Feature Map drafts
  exist, instead of silently exposing free-text entry — this does not change
  the generate API or block generation. The `PrerequisiteChecklist`
  (`components/prerequisite-checklist.tsx`, snapshot / symbols indexed /
  profile draft presence) is shared between Capability Map's and Feature
  Map's empty states rather than duplicated. Capability Map's gap links to
  `/system-understanding` carry `?capability=<key>` like other
  capability-context links on that page. Connect SDK links forward to
  `/setup-guide` (which already links back), closing the one-way link.
  All of the above are deterministic presence/routing checks — no heuristics.

- GitHub publish workflow (Issue #216, `pages/github.tsx`, nav item
  "GitHub"): App status card (`GET /github/app-status`; shows a setup hint
  and disables connection creation when not configured); Connections tab
  (list with status/default_branch/last_synced/last_error, a create dialog
  that lets the developer pick a repo from
  `GET /github/installations/{id}/repositories` or type owner/repo, plus
  verify/sync/disconnect); Publish Jobs tab (list with a status badge, a
  create dialog that reuses `useProbePatches()` filtered to patches whose
  latest baseline+probed validation runs both succeeded, and a detail
  dialog). The detail dialog renders the state machine's current status,
  `validation_summary`, requested/approved-by (username only when
  `useAuth().isAdmin`, since `GET /users` is admin-only — otherwise "User
  #id"), the sanitized `error` verbatim, and branch/commit/PR links built
  client-side from the connection's `owner`/`repo`/`web_base_url` (the API
  never returns a token or absolute host path, and the UI must not
  construct one either). Approve is a confirmation dialog only enabled for
  `status === "awaiting_approval"` that shows the publish target, branch
  name, and patch diff before calling
  `POST /github/publish-jobs/{id}/approve`; Cancel is only offered for
  `pending`/`awaiting_approval`. `usePublishJobs`/`usePublishJob`
  (`api/hooks.ts`) poll every 2s while any job is in a non-terminal,
  non-`awaiting_approval` status (`pending` through `creating_pr`) —
  `awaiting_approval` itself does not poll since it is a stable
  human-wait state, matching the System Understanding build-job polling
  pattern.

## Authentication model

- The session token from `/auth/login` lives in `st.session_state` only
  (no persistent login in MVP) and is sent as `Authorization: Bearer`.
- A session token takes precedence over `DASHBOARD_API_KEY` / `PROBE_API_KEY`;
  the env keys remain as service/fallback credentials sent as `X-Api-Key`.
- Gate UI by `/auth/me`: the My Tokens tab needs a user principal, the
  User Management tab needs role `admin`. Anonymous / legacy API key
  callers see neither.
- Show the raw token only once, right after issuing it, together with a
  `PROBE_API_KEY=...` snippet.

## Rules

- Prefer clarity over visual polish in MVP.
- Make component_id visible.
- Make current output and candidate output easy to compare.
- Do not expose replace mode controls in MVP unless explicitly added later.
- Show server/API errors clearly.
- Never write raw tokens or passwords to logs or persistent storage.
- Clearly distinguish `mock`, `running`, `failed`, and persisted real data.
- Show the pinned commit and evidence path/line range for intelligence results.
- Show decision method (`deterministic`, `reasoning_llm`, `manual`) and model
  audit metadata where an LLM result is displayed.
- Never display heuristic output as a fallback for reasoning-required work.
- Separate deterministic raw metrics from LLM interpretation/recommendation.
- LLM recommendations must not create automatic approve/adopt/apply controls.
- Keep dangerous actions disabled until their owning backend issue is complete.

## Verification

For UI-only changes, provide manual verification steps if automated tests are not available.
Verify system switching does not leak repository, Feature, plan, or experiment
data across Systems.
