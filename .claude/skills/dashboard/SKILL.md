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
- Simulation Workbench (Issue #242 Phase D / #246,
  `pages/simulation-workbench.tsx`, route `/simulation-workbench`): trace-row
  actions on Components Traces, Trace Lineage, and analyzer example rows
  (`components/replay-row-actions.tsx`:
  `ReplayabilityBadge` + Replay / Add-to-Replay-Set / Create-Experiment,
  plus the `trace` `AddToWorkspaceButton`), and a 3-pane page — Replay Set
  (traces + `JsonTree` input_capture + recorded output + per-trace
  input_source/skip guidance), a center editor (pinned-snapshot source in a
  Textarea; Direct edit → auto-diff via `POST /replay-source-diff` / Paste
  patch / LLM draft with provenance + `is_mock` badges), and the result
  matrix (match / diff / candidate_error / error_to_success distinctly
  badged, `field_diffs`, duration Δ, aggregate row). An always-visible
  simulation disclaimer, a confirm-gated Approve/Revoke approval panel
  showing the deterministic risk context, and escalations that only ever go
  through existing human gates (only successfully applied/completed variants
  may be promoted to Experiment via patch prefill; a provenance-bearing,
  fail-closed `reasoning_llm` regression-test scaffold; a static
  `set_candidate` live-shadow snippet). Source edits, pasted patches, and LLM
  drafts always carry the source/draft's pinned `snapshot_id`. Replay
  judgement/execution stays in the Phase A–C APIs; the regression scaffold is
  the isolated review-only reasoning boundary. The UI never writes to the
  target repo.
- AI Candidate Studio (Issue #252, `pages/candidate-studio.tsx`, route
  `/candidate-studio?session_id=...`, nav item under "Detail views", `Bot`
  icon): a conversation + candidate-versioning page over the same isolated
  Replay stack. A start view picks a component (and optionally a trace, or an
  advanced-collapsed existing Replay Set) then `POST /candidate-sessions`; the
  studio view has a left conversation pane (`POST .../messages`) and a right
  pane with `差分`/`全コード`/`評価結果` tabs for the selected
  `CandidateVersion`. The evaluation tab reuses the extracted
  `components/replay-result-matrix.tsx` (`ResultMatrix`, fetched from the
  existing `GET /replay-variant-runs/{replay_run_id}`) and the approval gate
  reuses the extracted `components/replay-approval-panel.tsx` (`ApprovalPanel`)
  — both shared verbatim with the Simulation Workbench. Exactly one
  state-driven primary action is shown: 候補を生成 (`POST .../generate`) →
  Replayで確認 (`POST /candidate-versions/{id}/replay`) → AIに修正を依頼
  (generate with `parent_version_id`, on a failed replay) → Experimentへ送る
  (`POST /candidate-versions/{id}/promote`, navigating via the existing
  `/experiments?replay_run_id=&replay_variant_id=` prefill). `is_mock` badges
  mark mock LLM output. Entry points: a component-level "AIで別バージョンを
  作る" (`pages/components.tsx`) and a trace-level "この入力から改善する"
  (`components/replay-row-actions.tsx`). No new judgement/execution/comparison
  logic; promotion never auto-creates an experiment and the UI never writes to
  the target repo.
- Decision Workspace tab (Issue #38): workspace list/create/switch, a
  conversation thread with grounded findings/assumptions/missing information
  visually distinguished, pinned context with links back to the owning
  Feature Map/Components/Probe Planner/Experiments tab, and proposal
  accept/reject with a required reason. There is no "defer" decision or
  proposal-edit action in the API (Issue #35 only exposes accept/reject); do
  not add UI controls for actions the API does not support.
- System settings diagnostics (Issue #101; badge source unified in #239): a
  header alert badge (`components/diagnostics-badge.tsx`) fed by
  `GET /system-state` `notification_items` (severity/scope/phase-filtered by
  the server and defensively deduped by `dedupe_key`) — never re-derived from
  audit-only `items`. The former `GET /system-diagnostics` direct
  read/fallback was removed in Issue #239. Diagnostics are still consulted
  only when an actionable canonical StateItem explicitly has
  `evidence.fix_kind=dialog`, to resolve that check's EnvFixDialog contents
  via `related_checks`; they never create the CTA. `user_action_kind=none`
  and `wait` items have no CTA. When `/system-state` cannot be loaded the
  badge shows an explicit degraded state (`?` + error dialog,
  `data-testid="diagnostics-badge-error"`); it never re-derives state
  client-side. Clicking an item navigates via `systemStateTarget()`. The
  System Understanding page shows a "Why?" button on missing/blocked
  pipeline rows that expands the related diagnostics. Diagnostics are
  deterministic server output — never decorate them with client-side
  heuristic explanations.
  Issue #115: the dialog text is Japanese and each problem is clickable.
  A `fix_kind: navigate` check routes to `fix_page?diagnostic=<id>&fix=<anchor>`
  and closes the dialog; a `fix_kind: dialog` check opens an env-var
  remediation dialog (which env vars, plus restart/re-run steps). Target
  pages render a `diag-anchor` on the fix control and an inline
  `DiagnosticFixCallout` (`components/diagnostic-fix.tsx`) that highlights the
  control and shows 原因 / 次の操作 verbatim from the diagnostic — no
  client-side interpretation.
  Several checks may share one `fix_anchor` (e.g. `llm_last_run` and the
  pipeline checks all fix at "build"): an exact `?diagnostic=<id>` match
  always wins, but the anchor-only fallback in `useFocusedCheck` picks the
  most severe check by the finite `SEVERITY_ORDER` (exported from
  `diagnostics-badge.tsx`), never backend array order — an informational
  `unknown` check must not shadow an actionable warning/error.
  The pipeline checklist's "Review interview proposals" CTA is driven by the
  structured `pipeline_capability_hierarchy` check (`fix_kind: navigate`,
  `fix_page: /interview` — the same object shown in the row's "Why?" list),
  never by regex-matching the step's free-text `detail`.
- Notification surfaces (Issue #239): every notification surface consumes
  `GET /system-state` projections only — page banner
  (`SystemStateBanner`) reads `page_items[currentRoute][0] ?? primary_item`
  (on System Understanding, non-error/blocked items are held back while a
  build is running, a deterministic condition); the header badge reads
  deduped `notification_items`; the persistent notice reads
  `notification_items[0]`; the
  Pipeline Checklist CTA reads the `StateItem` whose
  `related_pipeline_steps` names the first incomplete step (the old
  `STEP_CTA` map survives only as last-resort fallback); the header
  `UserPhaseIndicator` renders `user_phase` / `phases` verbatim. Never
  derive state, phase, or copy client-side; withdrawal is only fact
  resolution or phase suppression (no dismiss flags). A legacy server
  response still carrying the removed `primary_action` / `next_actions` /
  `understanding_refresh_recommended` fields must not resurrect old
  projections (contract-tested).
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
- Interview UX 評価指標 entry point (Issue #341):
  `components/system-understanding/interview-metrics-panel.tsx` is secondary
  observation information, so the cards are COLLAPSED by default behind a
  permanent labelled button near the page heading. Never restore the
  always-expanded card wall above the primary interview actions. The entry
  shows one of four states as TEXT, not colour alone — 正常 / 要確認 N件 /
  データ不足 / 取得失敗 — where 取得失敗 is derived client-side (a server that
  cannot answer cannot report its own failure) and never blocks the interview.
  「値が悪い」 (要確認, destructive) and 「まだ判断できない」 (データ不足,
  neutral) must not share a badge variant. Expanded content is ordered
  要確認事項 → データの評価可能性 → 全指標; the entry is a native `<button>`
  with `aria-expanded`/`aria-controls` and the panel is a labelled `region`.
  Per-metric state comes from the server's `attention` object — the client
  never re-derives a judgement, and an unmeasured metric is never rendered as
  a zero.
- Interview page state-driven UX (Issue #342 spec, implemented by Issue
  #349): before changing anything on `pages/interview.tsx` or
  `components/system-understanding/*`, read
  `docs/system-interview-workflow-ux.md`. It describes what the screen
  ALREADY does. The developer-facing state comes from ONE query,
  `useInterviewWorkflowState` → `GET /interview/workflow-state`, which
  returns the state (`W0-A`/`W0-B`/`W1`-`W7`), the single `primary_action`
  for it, `reached_state`, any pending backward request, and the currently
  active exceptions. Never re-derive a workflow state client-side, and never
  feed a mutation's `isPending`, a chosen tab, or any other client-only
  value into what is displayed — those vanish on reload (原則 P9).
  Concretely, on this page:
  - `components/system-understanding/workflow-panel.tsx` owns `R1`
    (`WorkflowLocationCard`: state label + 次にやること + progress steps, one
    per screen), `R5` (`WorkflowExceptions`: blocking/degraded ONLY —
    informational exceptions like `E8`/`E9` are branches of the primary work
    card and must never render as a warning band), and the `E8` lead
    (`BackRequestNotice`).
  - Exactly one work surface renders at a time
    (`data-testid="work-surface-W2".."work-surface-W7"`). There are no main
    tabs and no tab-default heuristic; `W3` and `W4` are separate states.
  - The state's primary action must BE its completion condition. `W6`'s is
    「この差分を確認した」 (`POST .../diff-review`, a manual record);
    downloading the `.patch` and opening the diff are auxiliary and never
    complete the state.
  - Do not add a control rendered disabled with explanatory text for an unmet
    precondition — show it only once it is usable (原則 P3).
  - Do not add a permanent manual trigger for a process satisfying the
    `A1`-`A4` automation gate (understanding build/refresh, alignment build,
    intent proposal, question routing/investigation, proposal generation,
    diff materialization, Runtime Reality Check). Those controls belong to
    the failure path only, gated on the matching active exception
    (`showRecoveryBuild` on `ReviewQueuePanel`, `showRecoveryActions` on
    `QaPanel`, the `E3-a`-gated understanding rebuild). Recovery offers only
    "run the same process again" and "leave safely to the `W7` terminal" —
    never a bypass of a human gate.
  - **Any mutation that changes a state-deciding fact must call
    `_invalidateWorkflow` (api/hooks.ts).** The page renders one
    server-decided state, so a missing invalidation is a stuck screen, not a
    stale badge — and since the spec removed the manual 「差分を生成」 CTA,
    `W5` wedges until a reload. The workflow query only polls while
    `running_processes` is non-empty, so a stale cache never starts polling
    by itself.
  - Panels follow the §3.3 state × role matrix. `W1` in particular shows the
    location card and the currently-active exceptions and NOTHING the running
    process consumes — offering Intent / Q&A / まとめて修正 edits while the
    card says 「操作は不要」 both contradicts the state and races the process.
  - One retry per exception: the recovery button lives in the
    `WorkflowExceptions` card (§4.4 puts failure, impact, recovery condition
    and retry in one frame), never also in the panel the process feeds.
    `E14` is the exception — it has no card of its own, so its retry sits in
    the failing process' own panel.
  - `R6` (history/audit) lives behind the single always-openable
    「履歴と監査情報」 entry in a fixed order, and that is where #341's
    metrics panel now lives — it is `R6` in every state and never `R2`.
  - The `W7` terminal (`completed`/`handoff`/`suspended`) decides the primary
    action; 中断・引き継ぎ・再開 go through `POST .../close` / `.../reopen`,
    which are manual audit records and never resolve a blocking failure.
  - Auto-selecting a session from the list happens ONCE, on first load with
    no `?session=`, and never overrides an explicit 「セッション未選択」.
    Re-selecting the newest session on every render makes `W0-B` unreachable
    on any System that already has a session — the developer can never start
    a second interview.
  - 「インタビューを開始」 posts `POST /interview/sessions` and stops there.
    The server dispatches the initial understanding build itself, so a
    follow-up `update-understanding` from the client runs the same reasoning
    build twice. Progress shows as `W1`; failure shows as `E3-a` with its
    retry.
  The spec relaxes no human gate and changes nothing about #341's metrics
  panel beyond its placement and its `R6` role.
- Interview page layout (Issue #295) — **superseded by Issue #349**: the
  two-tab main area and its default-tab derivation described below no longer
  exist. Kept for context on why the compensating machinery was there. The
  main column was a two-tab area —
  「Alignment Review」 and 「会話」. The default tab is derived
  deterministically from existing client state (`uiState`,
  `canConfirmStructuredUnderstanding`, `alignmentBuilt` from the
  `/alignment` response item count) — no server flag either way. Alignment
  Review is the default ONLY when alignment has been built AND the
  conversation tab currently has no required action (i.e. `uiState ===
  "proposal_review"` and `canConfirmStructuredUnderstanding` is false);
  every other `uiState` (`preparing`/`needs_build`/`confirm_understanding`/
  `fill_gaps`/`zero_base`/`ready_for_proposals`, including the
  `proposalNarrowing` sub-case) means a required action still lives in the
  conversation tab, so that tab stays the default even when alignment is
  already built (PR #296 2nd-pass review fix, Finding 4 — a "build済みなら
  Alignment Review" rule with no such check previously hid the required CTA
  behind the other tab). The explicit tab picker (`manualMainTab`) always
  wins once the user has switched tabs for that session. Because the
  `NextActionBanner` sits above both tabs, whenever a required action lives
  in the conversation tab and the currently-shown tab is not it, the banner
  renders an explicit 会話タブへ移動 button (`next-action-go-to-conversation`)
  rather than ever pointing at a control hidden in the other tab.
  The Alignment Review tab composes an Intent/現状/gap summary
  header (`AlignmentSummaryHeader`) on top of the unmodified
  `ReviewQueuePanel`; the sidebar holds auxiliary panels only (Intent Brief
  editing, understanding overview, Inquiry, handoff, Q&A list) and never
  duplicates the Review Queue. The gap summary shows the top 1-2 outstanding
  gaps' own text (must_review items, or any item with `alignment_state ===
  'gap'`, excluding answered/corrected/superseded rows — same "outstanding"
  definition as `outstanding_counts` below) plus a "ほか N件" count, not just
  a bare count; it falls back to a "no gaps" message when there are none.
  Actionable category counts (要確認/一括レビュー可, both in this header and
  in `ReviewQueuePanel`'s own category summary) prefer the `/alignment`
  response's `outstanding_counts` (未対応件数: `superseded=0` AND status not
  in answered/corrected) over the total `counts`, so the displayed number
  always matches the Review Queue's actual action-card count; an older
  Control Server without `outstanding_counts` falls back to `counts`.
  まとめて送信 (`answers-batch`) sends each staged item's `content_hash` (as
  read from the last `/alignment` response); a per-item failure on an entry
  that carried a `content_hash` is shown as "項目が更新されています" and the
  item stays staged for retry, same as any other partial batch failure.
  All 「わからない」/「AIに先に調査させる」 investigation calls go through
  the single `useQaAutoInvestigate` controller (`api/hooks.ts`) — do not
  add another route-and-investigate call site; per-question calls must
  pass `qa_ids: [id]` so one user action never fans out to a multi-question
  LLM investigation.
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
- Phase-based prerequisite guide (Issue #241): `PrerequisiteGuide`
  (`components/prerequisite-guide.tsx`) answers "why is this empty / where do
  I go next" from `GET /system-state` alone — it shows the current
  `user_phase` (via `USER_PHASE_LABELS`) and the phase-scoped `primary_item`'s
  server copy + `systemStateTarget` CTA, and renders nothing at the terminal
  `diagnosis` phase (so it disappears as the phase advances). Used in
  Overview's zero-component state, Feature Map's empty features state, and the
  Probe Planner generate dialog when `phases`'s `preparation` is not
  complete. The Probe Planner use is a steer, not a block: the manual
  feature-id escape hatch and the generate API are unchanged. Setup Guide now
  also links back to Connect SDK (`setup-guide-connect-sdk-link`), making that
  pair bidirectional. Never derive phase or state copy client-side.
- **"Phase 0" pre-login / zero-System guidance (Issue #265)**: everything
  above (`GET /system-state`, `PrerequisiteGuide`, `DiagnosticsBadge`) needs
  a selected System, so it is all disabled before login and while
  `systems.length === 0`. `useBootstrapStatus()` (`api/hooks.ts`, `GET
  /auth/bootstrap-status`) is the one hook not gated on `getSystemId()`, and
  is the only source for this phase's copy: `pages/login.tsx` replaces the
  username/password form with static bootstrap instructions
  (`CONTROL_ADMIN_USERNAME`/`CONTROL_ADMIN_PASSWORD` + restart) when
  `admin_exists === false`, reducing detail in production
  (`environment === "production"` hides the specific env var names and
  shows a generic "ask your administrator" message instead — Issue #225's
  fail-closed spirit applied to wording, not to the fact itself).
  `components/layout/header.tsx`, `pages/overview.tsx`, and
  `pages/settings.tsx` each add an explicit `systems.length === 0` branch
  (`data-testid`s `header-no-systems-hint`/`header-create-system-button`,
  `overview-no-systems`, `settings-no-systems-reason`) pointing at the
  header's "System を作成" control, instead of the icon-only "+" dead end /
  the System-scoped get-started list / the heading-only blank screen that
  existed before. This bootstrap copy is purely client-fixed (not returned
  by the endpoint, which is facts-only), so per the #240/#266 catalog policy
  it stays plain Japanese literals in the tsx files rather than new
  `state_messages.py` entries. No new `user_phase` value: phase 0 is
  client-side display branching on this one endpoint's booleans, layered in
  front of the existing System-scoped phase model, not a change to it.

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

## System Purpose alignment view (issue #275, formerly #94)

- The System Understanding page's purpose section renders
  `purpose_views` from `GET /repository/system-understanding` as
  side-by-side cards — 「人の認識(System Profile)」 (`provenance_kind:
  manual`, snapshot-independent) and 「AI/ソース由来の理解」 — with the
  provenance badge pattern reused as-is. Do not render the legacy single
  `purpose` field in this section; it stays for other consumers.
- When the manual side is empty, the section offers inline purpose entry
  via the existing `PUT /system-profile` (merge the current profile from
  `useSystemProfile()` and change only the purpose; never clobber other
  fields). On success invalidate the system-understanding, system-profile,
  and system-state queries so the view updates without a reload.
- When both sides exist, the 「一致を確認した」 action posts
  `POST /repository/system-understanding/purpose-confirmation`; a valid
  confirmation renders as 確認済み + timestamp, and `stale_reason`
  (`profile_updated` / `snapshot_changed` / `ai_updated`) maps to fixed
  Japanese notes. The client never judges match/mismatch itself — it only
  records the human's confirmation.
- The section carries anchor id `purpose-views` so the
  `understanding.purpose.manual_profile_unconfirmed` StateItem's
  `target_ui` deep link resolves.

## UI Language Convention (Issue #266)

Dashboard-side hardcoded UI copy follows the same Japanese-canonical
convention as the server's `state_messages.py` catalog (#240). The rule
(codified in `CLAUDE.md`'s "Dashboard UI言語規約" section): user-visible copy
is Japanese; technical identifiers/proper nouns (System, Trace, Replay,
Experiment, Snapshot, Capability, `off`/`trace`/`shadow`, GitHub, PR, branch
names, HTTP codes, env var names, and established product-concept page/
feature names) stay as-is.

Operational notes when touching a screen:

- **Same-screen CTA groups must not mix languages.** Before adding or
  changing a button/toast/heading, check every other user-visible string
  rendered on the same screen state (including shared components mounted
  there, e.g. `ApprovalPanel`, `AddToWorkspaceButton`, `ResultMatrix`,
  `ReplayRowActions`) — translating only the page-local strings while a
  shared component next to it stays English recreates the same mixing bug.
- **Constant-coupling**: some client literals are paired with a server
  catalog key by exact string match, not by API contract. Known pairs:
  `gap-worklist.tsx`'s `CREATE_ISSUE_ACTION` must stay identical to
  `state_messages.GAP_CREATE_ISSUE_ACTION`. Before renaming/retranslating any
  string that also appears in `apps/control-server/app/state_messages.py` (or
  is compared against a server response elsewhere), grep both sides first.
- **Finite-set/enum-shaped labels** (case status values like `match`/`diff`/
  `candidate_error`, replayability values, policy modes) may stay in their
  English canonical spelling even in an otherwise-Japanese screen — they are
  technical identifiers, not prose, per the identifier exception above.
- **Client-side fallback strings** (used only when a server field is missing,
  e.g. `STEP_LABELS`/`USER_PHASE_LABELS` last-resort maps) must be Japanese
  too — a fallback is still user-visible copy.
- Do not touch `apps/control-server/app/state_messages.py` contents for this
  convention — that catalog is already Japanese and owned by #240; report
  any genuinely-English leftover found there instead of editing it here.
- `data-testid`, API field names, schema fields, and log lines are not
  user-visible copy and are out of scope for this rule.
- Exhaustive translation of every minor label in a single change is not
  required; the acceptance bar is that no single screen/CTA group mixes
  languages. When leaving a string in English for effort reasons, prefer
  whole uniformly-English pages (no existing Japanese on that screen) over
  leaving a partial mix.

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

## Cell Fabric page (issue #303)

- `src/pages/cell-fabric.tsx` (route `cell-fabric`, sidebar "Cell Fabric")
  renders `GET /cell-fabric/root-digest` as 4-level progressive disclosure:
  結論 (always visible) → 詳細 → エビデンス → 監査情報, collapsed by
  default. UI copy is Japanese (Issue #266); Cell / Trace / severity codes
  stay canonical.
- The Ask list posts to `POST /cell-fabric/asks/{id}/decide`
  (承認/保留/却下). Keep the fixed helper text 「承認は提案の受け入れのみを
  意味し、実行には別途承認が必要です」 — proposal accept never means
  execution approval (`execution_approved` stays 0 server-side).
- Tests: `src/__tests__/cell-fabric.test.tsx`.

## Interview 画面の「現在のシステム理解」 (issue #351)

- `components/system-understanding/understanding-brief.tsx` renders
  `GET /interview/understanding-brief`. The confirmation state, the
  provenance, the readiness verdict and every reason string come from the
  server. Do not recompute any of them client-side — the only client table is
  `BRIEF_DISPLAY_BY_STATE` (display density per workflow state), and the
  workflow state itself still comes from `GET /interview/workflow-state`.
- Placement is fixed: top of the MAIN column, in every state. The right
  column wraps below the main work on narrow screens, so a Brief living there
  pushes Vision / Purpose / 進行可否 under the current task. There is exactly
  one Brief on the page; do not add a second copy of 「現在の理解」 anywhere.
- Display rules that are requirements, not styling:
  - Every state badge carries text (never colour alone), and 確認状態 /
    出所 are two separate badges — collapsing them into one loses the
    distinction the whole feature exists for.
  - No composite confidence percentage. 「警告 N 件」 alone is never the
    reason for a readiness verdict; a reason always names its target.
  - Evidence, API boundaries, elements, and the full understanding tree stay
    behind a disclosure. The 前回確認後の変更 notice does NOT: a change the
    developer would miss while collapsed defeats its purpose.
  - Before the understanding is built, show that fact — never a placeholder
    Vision or Purpose.
- `W2` is the only state where the Brief carries the primary action
  (「この理解で進む」). Keep the judgement target and that button in the same
  card. The conversation card's remaining confirm button is the zero-base
  path only (no structured understanding to show).
- If the Brief request fails or the server predates the endpoint, the panel
  degrades to `data-display="unavailable"` and still renders `primaryAction`
  and `fullTree`. Never let a summary failure hide the state's primary action.
- Mutations that change a state-deciding fact must invalidate the Brief;
  `_invalidateWorkflow` in `api/hooks.ts` already does both.
- Tests: `src/__tests__/understanding-brief.test.tsx`, plus the Interview page
  group in `dashboard-contracts.test.tsx` (which routes
  `/interview/understanding-brief` through `mockInterviewApi`).

## Interview コックピット (issue #356)

The Interview page's overview layer, on top of #349's state machine and
#351's Brief. Page heading is 「インタビュー・コックピット」.

- `components/system-understanding/cockpit/model.ts` is the ONLY place that
  aggregates or classifies. It is pure (no React, no API client) and unit
  tested; display components render its output and must not re-derive a
  category status, a completion number, a priority order, or an action's
  availability. Adding backend endpoints for this page is out of scope —
  every number comes from responses the page already fetches.
- The category status set is EXACTLY `confirmed`/`review`/`missing` (issue §3)
  — never add a fourth value for a data-availability condition. A status that
  cannot be settled is `status: null` (label withheld), and the availability
  itself lives on the separate `qaFetchStatus` axis.
- The five categories, their status, and the
  completion percentage are deterministic: content presence, exact-name gap
  matching (same rule as `understanding_diff`), then `gap_type`'s default
  category. No similarity, no keyword scoring. `vision` is an optional key
  (#352) — a response without it must render as 未設定, never crash.
- Q&A progress must satisfy 回答済み + 確認待ち + 未回答 = 合計. ONLY
  `answered` and `revised` (superseded history) leave the unresolved list and
  the total. **`skipped` is not resolved** — server-side it is the temporary
  「後で回答」 state that `resume` puts straight back to `open`
  (`routes/interview.py`), so it counts as 未回答 (re-stated as a 「後で回答
  N 件」 breakdown and a row marker). Excluding it makes a session with
  unanswered questions read as 未解決 0 件 / 完成度 100%. `open_questions` and
  `interview_qa` rows are MERGED by `qa_id` (then question text), never
  "keep the first, drop the rest": only the `interview_qa` row carries state
  — skip/resume never touch the `session.open_questions` JSON — so the Q&A
  row's `unconfirmed`/`deferred` and its state-derived priority must be
  applied to the surviving row, and a question the Q&A side reports as
  `answered`/`revised` leaves the list even if `open_questions` still lists it.
- **0 件 and 「取得できていない」 are different displays.** Question totals,
  unresolved counts, and Q&A progress rest solely on `GET
  /interview/sessions/{id}/qa`; pass its query state in as
  `qaFetchStatus` (`ready`/`loading`/`unavailable`). When it is not `ready`:
  `model.qa` is `null` (never a zeroed progress object), `completionPercent`
  is `null` (no progress bar, a reason instead), a category that would be
  `confirmed` only because no question is outstanding gets `status: null`
  (the card shows 「Q&A 未取得のため保留」 instead of a status badge), the
  要確認 count is rendered as 「N 件以上」 (`countsSettled: false`) because 0
  would read as "nothing to do", and the Q&A card plus the map both offer a
  retry. `missing` and `review` are decided from the session detail alone, so
  they keep their normal 3-value badges.
- The detail pane's 「修正するには」 entries only scroll + focus an existing
  panel via `cockpit/navigation.ts`. Targets are an ordered candidate list
  (`unresolvedTargets()` + `focusFirstCockpitTarget()`), never one fixed id:
  anything tied to a specific question must target that question's row
  (`qa-item-<id>`) first and fall back to `work-surface-W3` / the unresolved
  list only when the row is not rendered. Focusing the work surface's first
  button sends the developer to a different question than the one they
  picked. Never reimplement answering, editing, or evidence display there.
  Unavailable entries stay visible as disabled + reason — this is the one
  deliberate exception to 原則 P3, which governs a state's PRIMARY action,
  not guidance about how to fix an item.
- Availability is decided from the server's workflow state (`W2`/`W3`/`W4`
  for editing, `W3` for answering). Do not re-derive the state.
- Placement is part of the contract: the status summary is full-width ABOVE
  the two-column grid (so it does not move when the right column wraps), the
  Brief stays at the top of the main column (#351), the map sits directly
  under it, unresolved items + Q&A progress sit BELOW the state's work
  surface, and the detail pane heads the right column with the session-info
  card (participants / last update / evidence counts / save state) replacing
  the old 「セッション #id」 card rather than adding a second one.
- A failed 「セッション一覧」/「セッション詳細」 query must render the
  `interview-load-error` card (failed target + server reason + 再試行), never
  an empty body: without either query the page has nothing to draw, and the
  old silent-blank behaviour hid both the failure and the next step.
- Accessibility requirements, not styling: map cards are native `<button>`s
  with `aria-pressed`; every status is text as well as colour; the donut has
  `role="img"` + a full `aria-label` and the same numbers in a text list; the
  progress bar carries `role="progressbar"` + `aria-valuenow`.
- The `/interview-mock` route and `pages/interview-mock.tsx` were deleted
  with this implementation. Do not reintroduce a static mock page.
- Tests: `src/__tests__/interview-cockpit.test.tsx` (model + components) and
  the Interview page group in `dashboard-contracts.test.tsx`. Interview page
  queries in that file must be scoped (e.g. `within(...
  interview-proposal-card)`) — the cockpit's disabled reasons quote workflow
  state labels, so a bare `/承認/` or `/編集/` role query now matches more
  than one button.

## Interview コックピットの情報設計 (issue #358, subs #359-#363)

Layered on #356. Everything in the #356 section above still holds — this
adds the ordering, density, and width rules. Dashboard-only: no endpoint, no
mutation, no permission change.

- **The first view leads with the action.** `CockpitStatusSummary`'s primary
  element is 「次にやること」 + exactly ONE CTA; 完成度 is one tile among the
  counts. The CTA **navigates only** (scroll + focus) — the state's primary
  action stays the single executor inside its work surface (原則 P1). Only
  `state_primary` takes its label from the server's `primary_action`; the
  page supplies it, `model.ts` returns `actionLabel: null`.
- `CockpitModel.nextStep` is a first-match finite table (`retry_qa` →
  loading → missing category → unresolved question → review category →
  `state_primary`). Not a score. Adding a branch means adding a row.
- **Main-column order is contract**: 現在地 → 例外/戻り要求 → status summary
  → the state's work surface → 未解決事項 + Q&A 進捗 → 全体像 (Brief, map).
  This supersedes #351's 「Brief at the top of the main column」 for
  `W3`-`W7`. In `W1`/`W2` (and with no workflow state) the Brief IS the work
  surface — `W2`'s 「この理解で進む」 lives in it — so it leads there. Render
  it from ONE value; never two instances.
- **Unresolved grouping key is the finite category key only** (5 keys +
  `null`). 「意味的に近い質問をまとめる」 is membership in an already-finite
  server-derived set — never similarity, embeddings, or keyword scoring
  (Principle 6). Top 3 groups initially; 「残り N 件を表示」 counts hidden
  *questions*; every non-representative question keeps its own open button
  or it becomes unreachable. Expanding focuses the first revealed row,
  collapsing returns focus to the toggle.
- **Any grid row pairing a tall card with a short one needs `items-start`.**
  CSS Grid's default `stretch` is what made the Q&A progress card 3,416px
  tall next to the unresolved list.
- **Map cards are a scan**: 番号 / 名称 / 状態 / one-line 要約 only; caption
  and hint live in the detail pane. `missing`/`review` get ring emphasis
  plus a 「要対応」 text marker (never colour alone). The fixed 5-category
  order never changes — do not sort by status. The detail pane is
  `xl:sticky`; below `xl` the map's 「選択中のカテゴリの詳細へ」 is the
  keyboard/mobile route to it.
- **Auxiliary information is one disclosure area**
  (`CockpitAuxiliaryPanel`/`CockpitAuxiliarySection`). Which sections exist
  is still #342 §3.3's state matrix — only the density changed. Three things
  never go inside a collapsed disclosure: a pending handoff list (it drops
  into the area only at 0 待ち), blocking/degraded failures (they stay in
  `WorkflowExceptions` above the summary), and the recovery actions of a
  currently-failed process (its section opens by default).
- **`focusCockpitTarget` opens every ancestor `<details>` first.** A closed
  `<details>` keeps its children in the DOM, so `querySelector` finds them
  and `focus()` then fails silently — the CTA appears to do nothing. When
  the target IS a `<details>`, focus its `<summary>`.
- Session number / Snapshot / status belong to `cockpit-header-meta` alone.
  `CockpitSessionInfo` must not repeat them; the header's 「セッション情報」
  button is its entry point.
- **Below `md` the sidebar is an overlay Drawer** (`AppLayout` owns the open
  state; the header's `md:hidden` menu button toggles it): focus trap,
  Escape, close on navigation, focus back to the toggle. At `md`+ the
  existing rail and its collapse/expand are unchanged. `main` is
  `p-4 md:p-6`. jsdom does not evaluate media queries — assert on the
  responsive class names plus real behaviour, not on a simulated width.
- Tests: `cockpit-unresolved.test.tsx`, `cockpit-map-detail.test.tsx`,
  `layout-navigation.test.tsx`, plus the Interview page group in
  `dashboard-contracts.test.tsx` (order, disclosure, header de-duplication).

## オーバーレイの面は 1 つの規則で揃える (`lib/modal-surface.ts`)

Any surface that covers the page content — the mobile nav Drawer (#362), the
assistant panel (#102) — uses `useModalSurface`. It is the single
implementation of four rules, and a second copy of them is how one surface
ends up without Escape or without focus return:

- focus moves into the panel on open,
- Escape closes it,
- Tab/Shift+Tab cycle inside it (focus never falls back to the page behind),
- focus returns to the element that was focused before opening.

The panel needs `tabIndex={-1}`, `role="dialog"`, `aria-modal="true"`, and an
accessible name. A backdrop that closes on click is the caller's job (the
hook does not render anything) and is **required** for a surface that can
cover the whole viewport — at 390px the assistant panel is full width, so a
single close button in the corner is not an escape route.

`returnFocusRef` is for openers that stay mounted (the header's menu button).
When the opener is unmounted while the surface is open — the assistant's
floating button is — the hook's default cannot work: the remembered node is
detached, and the button comes back as a NEW node. Focus the re-mounted
element from the component instead.

**The assistant's floating button and `<main>`'s `pb-24` are one pair.**
The button is `fixed` over the scroll area, so the padding is what guarantees
page content ends above it (#102: the assistant must not hide a screen's
primary action). Changing the button's `bottom-*` without the padding — or
the reverse — puts it back on top of the primary action at narrow widths.

### #358 のレビューで直した 3 点 (再発させない)

- **`xl:sticky` は右カラム(Grid の直接の子)に付ける。** 中の詳細ペインだけを
  sticky にすると、動ける範囲がその親=右カラムの内容高さ(約 875px)に閉じ、
  行の高さ(約 2,477px)ぶんスクロールする理解マップへ着く前に画面外へ流れる。
  Grid の直接の子なら containing block は行いっぱいの grid area なので、
  #359 のために必要な `items-start` と両立する。カラムごと sticky にするのは、
  詳細ペインだけを浮かせると通常フローに残る補助情報の上へ重なるため。
  背が高い場合に下端へ到達できるよう `xl:max-h-[...] xl:overflow-y-auto` を
  必ず添える。
- **主作業面は 1280 × 720 の初期表示に入っていること。** ソース順で上にある
  だけでは足りず、実測できる要件である。サマリーの統計は既定で閉じた
  `<details>` (`cockpit-status-stats`)、画面の説明文はセッション未選択時のみ、
  ページ root は `space-y-4`。`W2` の主操作「この理解で進む」は Brief カードの
  **ヘッダー側**に置く(カードが約 566px あり、本文の下だと初期表示に入らない)
  — カードは 1 枚、主操作も 1 つのままで #351 の条件は変わらない。上部に何かを
  足すときは、この予算を消費していないか実測して確かめること。
- **詳細ペインの展開状態はカテゴリ切替でリセットする。** 展開状態はコンポー
  ネントが持つので、リセットしないと 2 つ目以降のカテゴリが全件展開で始まり、
  段階的開示が最初のクリック以降ずっと無効になる。effect ではなくレンダー中の
  「props 変化に応じた state 調整」で畳む(effect だと一度古い状態で描いてから
  畳み直すちらつきになる)。

実ブラウザでの確認は、`container.innerHTML` を書き出して `dist/assets/*.css`
と一緒に静的 HTML へ入れ、Chromium (`/opt/pw-browsers/chromium`) で測ると
本番と同じ CSS のまま寸法を検証できる。Playwright はリポジトリの依存には
追加しないこと(この確認は使い捨てで行う)。
