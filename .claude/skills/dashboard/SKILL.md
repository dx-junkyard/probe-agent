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
- Interview page layout (Issue #295): the main column is a two-tab area —
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
