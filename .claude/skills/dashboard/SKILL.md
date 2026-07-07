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
- Connectivity guide & warning badge (Issue #165): `pages/setup-guide.tsx`
  (`/setup-guide`) is the Japanese client-setup instruction page. The
  developer picks their execution pattern from a finite set (host-direct /
  same Docker Compose project / add SDK to an existing image / external
  repository or compose project) and sees the env vars, compose snippets,
  token setup (no-auth local, legacy `CONTROL_API_KEYS`, issued API token),
  patch-apply commands (`git apply --check` → `git apply` → tests → commit),
  a manual smoke-trace `curl` (component_id `probe-smoke-check`), and a
  failure-isolation section (missing config / auth failure / network
  unreachable / no events sent / workload not run). A live connectivity
  status card polls `GET /connectivity/status`. `?session=<id>` renders a
  context banner for that interview session (snapshot, commit, patch state).
  Secrets policy: the page shows placeholders only — never real tokens.
  `components/connectivity-badge.tsx` renders in the header: state
  `no_signal` → warning badge 「シグナル未受信」, `smoke_only` → info badge
  「疎通確認のみ受信」; both link to `/setup-guide`; `receiving` hides the
  badge. Wording must stay observation-based (「まだ受信していない」), never
  「未設定」— the server cannot know why nothing arrived.
- Interview review-diff card (Issue #165): the card explains intent and
  provenance (generated from approved proposals, against the pinned
  snapshot/commit, changes are `probe-agent:` docstring metadata +
  `@probe` instrumentation, the target repository is NOT modified, the
  developer reviews and applies manually), offers a `.patch` download via a
  client-side Blob with filename
  `probe-agent-system{systemId}-session{sessionId}-snapshot{snapshotId}[-{commit8}].patch`
  (disabled with a reason when the diff is empty), shows `git apply`
  guidance, and after materialization links to `/setup-guide?session=<id>`
  as the next action.
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
