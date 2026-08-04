# probe-agent 開発指示

## Project Overview

`probe-agent` is a runtime probe and evaluation platform for tracing, comparing, and evolving software components.

The MVP focuses on Python functions and supports:

- `@probe(component_id="...")`
- input / output / error / duration tracing
- Control Server trace ingestion
- component-level policy
- `off` / `trace` / `shadow` modes
- shadow comparison between current and candidate implementations
- manual evaluation before adoption
- System-scoped repositories and runtime data
- LLM-backed candidate generation and evaluation

The next phases add a Feature Intelligence Layer and an isolated Experiment
Workspace. See `docs/project-intelligence.md`.

Do not implement unsafe automatic replacement in the MVP.

---

## Architecture

This repository is a monorepo.

```text
apps/
  control-server/     FastAPI server for traces, policies, and comparisons
  dashboard/          Simple dashboard for trace inspection and mode control

packages/
  python-probe/       Python SDK providing @probe

shared/
  schemas/            Shared JSON schemas and data contracts

examples/
  simple-pipeline/    Example app for validating the MVP

docs/
  design.md
  mvp.md
  project-intelligence.md
```

---

## Current Roadmap

Implement Feature Intelligence in dependency order. Do not skip ahead by
creating incomplete persistence or execution paths for later phases.

1. Issue #23 — Repository Understanding MVP
   - committed-files-only snapshot
   - evidence-backed System Profile / Feature Map drafts
2. Issue #24 — Feature-to-Code Mapping MVP
   - deterministic Python AST index
   - reasoning-model mapping from Feature to code symbols
3. Issue #25 — Probe Plan / Temporary Patch MVP
   - reasoning-model probe planning
   - approved instrumentation in an isolated worktree only
4. Issue #26 — Experiment Workspace Runner MVP
   - baseline and source-patch variants
   - deterministic metrics plus reasoning-model interpretation
5. Issue #144 — Trace Lineage & Dynamic Analysis track (sub-issues #145-#152)
   - entity/correlation/flow lineage, bounded projections, review-gated
     analyzers, shadow subset diffs, Flow Explorer runtime overlay
   - implement sub-issues in dependency order; see the Issue #144 section in
     `docs/project-intelligence.md` for the breakdown and design decisions
6. Issue #168 — Probe Pattern lifecycle
   - deterministic instrumentation scan, pre-release removal patches with an
     explicit apply boundary, pattern reconciliation against the latest
     snapshot (deterministic structural checks first, reasoning model for
     moved/split/missing, no heuristic fallback), and plan creation that
     reuses the #25 approval/patch/validate/apply gates
   - see the Issue #168 section in `docs/project-intelligence.md`
7. Issue #216 — GitHub App publish workflow: GitHub App auth / Installation
   Token broker / connection persistence, then a repository manager
   (mirror clone/fetch/worktree/cleanup), a publish job state machine
   (commit/push/PR), and Dashboard UI, implemented in that order.
   Issue #222 constrains this workflow to administrator-registered GitHub
   App installations for the configured Organization. An installation must
   also be explicitly assigned to a System before any repository listing,
   connection, verify, sync, or publish token issuance may use it.
8. Issue #242 — Replay / Simulation track (sub-issues #243-#246): replay a
   probe's captured real inputs against pinned-snapshot code (baseline
   simulation) and against edited/patched code (offline shadow), then promote
   promising candidates into the existing Experiment / live-shadow / test
   gates. Implement sub-issues in dependency order:
   - #243 (Phase A): opt-in structured input capture (`@probe(...,
     replay_capture=...)`, canonical JSON with a `"__probe__"` marker) +
     deterministic replayability classification + `shadow_result.schema.json`.
   - #244 (Phase B): a shared worktree/sandbox harness that imports the
     resolved symbol and calls it with restored inputs, a human replay
     approval gate (`decision_method: manual`), and the deterministic
     comparison of replay output vs recorded output. `generation.py`'s
     candidate execution is migrated onto this harness.
   - #245 (Phase C): patch variants (baseline + N candidates) run in
     independent worktrees with a finite diff matrix (match / diff /
     candidate_error / error_to_success / …), the `_field_equal` rules
     extracted to `app/comparison.py`, and LLM candidate drafts
     (`reasoning_llm`, fail-closed).
   - #246 (Phase D): the Simulation Workbench UI (`/simulation-workbench`),
     trace-row actions + replayability badges, and two deterministic
     source/diff endpoints for the edit→diff flow.
   See the Issue #242 section in `docs/project-intelligence.md`. Recorded-error
   traces are executed against candidates on the OFFLINE side only; the live
   SDK shadow asymmetry (`decorator.py`'s `run_shadow and raised is None`) is
   intentionally unchanged. Replay never runs an unapproved component, never
   writes to the target repo, and (Principle 4) targets pure-ish components
   only — payment/email/DB-write/auth are discouraged even with approval.

9. Issue #252 — AI Candidate Studio: a conversation-oriented workflow that,
   from a component or a specific Trace, prepares baseline commit / target
   symbol / Component Profile / Evaluation Criteria / Replay Set, lets the
   developer describe an improvement goal in natural language, and generates
   immutable candidate versions evaluated in the EXISTING isolated Replay
   infrastructure. It adds no new judgement/execution/comparison path:
   candidate-proposal generation is the reasoning-model structured proposal
   (summary / assumptions / changed_symbols / generated_code / risks /
   suggested_tests) + deterministic splice→diff of `app/candidate_studio.py`
   (built on `replay_draft`, fail-closed); replaying a version reuses `POST
   /replay-variant-runs` verbatim (its human replay-approval gate, network-off
   worktree sandbox, always-cleanup, finite diff matrix); promotion reuses the
   variant experiment-payload shape and never creates an experiment, writes to
   the target repo, opens/merges a PR, deploys, or enables live shadow. Only a
   successfully generated & validated patch creates a `CandidateVersion`; chat
   messages never do. See the Issue #252 section in
   `docs/project-intelligence.md`.

10. Issue #282 — Interview Alignment UX (sub-issues #283-#292): the AI
    investigates implementation facts first and asks the user only what
    only the user can decide. Implemented in dependency order:
    #283 段階表示 (progressive disclosure of 現在の理解), #284 Intent Brief
    (user intent separated from implementation facts; AI proposals never
    auto-confirm), #285 Inquiry lifecycle (doubt resolution is strictly
    separate from answer confirmation; 「疑問は解消した」 never confirms the
    original item), #286 Question Router (human_only / system_researchable /
    hybrid) + read-only Investigation Agent on the pinned snapshot with
    explicit budgets, #287 Alignment Review / Review Queue (deterministic
    first-match rule table for must_review / batch_reviewable / …; queue
    ordering is finite-gate based, never scored or LLM-ordered), #288
    auto-refresh after answer batches (interview_refresh_job; idempotent,
    stale-safe, answers survive refresh failure), #289 NL batch correction
    via structured change sets (resolved/ambiguous/conflict/stale/forbidden;
    preview + selective apply only), #290 Runtime Reality Check integration
    (finite match/mismatch/unobserved/stale states; new observations only
    via manual-approval proposals that never touch policies), #291 knowledge
    areas + handoff (answerable areas chosen by the user, handoff answers
    never recorded as the original user's answer). Per-issue design notes
    live in `docs/project-intelligence.md`.
    **Status: #282 and #283-#291 are implemented, verified, and closed.**
    **#292 (低リスク提案の一括承認) is intentionally NOT implemented** and was
    closed as `not_planned`, superseded by #311: its start condition — observed
    usage data and misclassification/undo cases from individual review — is not
    yet met. Do not implement it until that data exists; #309 provides the
    measurement. Remaining work is tracked under Epic #307.

11. Issue #295 — Interview Alignment UX 差分改善: the original UX proposal
    behind #282. Most of it is already covered by #283-#291; the implemented
    delta is: `review_category='unchanged'` made reachable via deterministic
    content-hash carry-over of previously `accept_current`-answered items
    only (needs_change/reject/corrected stay actionable and are fed into the
    next Understanding review; with a goal-change guard that disables
    carry-over for that rebuild), Review
    Queue category count summary + local batch answering (staged answers
    sent through the existing per-item `/answer`; NOT AI batch approval —
    `decision_method: manual` stays per item), audit-detail expansion and
    deterministic sampling of no-review-required items, evidence shown
    up-front for conflict/high-risk/runtime-mismatch/single-evidence items,
    4-stage progressive disclosure in Inquiry answers, and 「わからない」
    auto-routing into the existing route-and-investigate flow.
    **Status: implemented, verified, and closed.** Deferred work now has its
    own issues under Epic #307: Inquiry snapshot/revision premise tracking +
    `superseded` (#308, **implemented and verified** — an immutable premise
    bundle captured at Inquiry creation, a review-subject identity built only
    from structural anchors, a deterministic premise evaluation inside the
    Alignment rebuild transaction, and a terminal system-only `superseded`
    status), sample-error driven rule re-evaluation (#310), and
    the §5.5 re-confirmation cascade beyond goal/Intent (#312,
    **implemented and verified** with an authenticated System-wide canonical
    head, stable Capability identities, manually editable many-to-many support
    relations, and relation-scoped carry-over invalidation). #309 is
    implemented: deterministic System-scoped UX metrics use persisted facts
    and finite UI events, return unavailable values as `unmeasured`, and keep
    guardrails separate without changing product behaviour. #341 builds on it:
    the metric cards are collapsed behind a permanent labelled entry that shows
    正常 / 要確認 N件 / データ不足 / 取得失敗 as text, and the `guardrail`
    designation is separated from the evaluated 要確認 judgement, which comes
    from the fail-closed `app/policies/interview_metric_attention.yaml`
    artifact (direction / minimum sample / window / trigger / clear condition;
    numeric thresholds are deliberately left unset and decided from real data
    later). #313 is implemented: the
    no-review-required policy is now a schema-validated, fail-closed YAML
    artifact with version/digest provenance on every new Alignment item. #292 remains NOT
    implemented (superseded by #311). The Inquiry status set stays at the
    current 5 values and Intent Brief field names stay as-is — see the
    Issue #295 section in `docs/project-intelligence.md` for why.

12. Issue #297 — Probe Cell Fabric (sub-issues #298-#304): assign a logical
    Probe Cell to each approved Probe Point / Component (1 probe = 1 logical
    Cell, `system_id + component_id`; never a resident LLM process, never a
    per-trace LLM call), aggregate state through Feature/UX/API/Flow domain
    orchestrators, and unify user interaction in a Root Orchestrator.
    Implement sub-issues in dependency order:
    #298 Cell contract / versioned Agent Role Card (distinct from the #58
    API Role Card; model aliases only, never literal provider/model names) /
    common `cell_state` schema; #299 versioned Cell Binding from approved
    Probe Points/Patterns only + a read-only Probe Cell pilot (structural
    drift detection, never heuristic re-binding); #300 Goal/Task ledger with
    single owner + single `parent_goal` per task, acceptance+evidence
    required for `done`, digest/escalation-only report contract
    (fail-closed), idempotent resends; #301 domain orchestrators
    (deterministic bottleneck candidates; systemic-vs-individual triage is
    reasoning_llm fail-closed; span-of-control ≤7, depth ≤3, static
    rosters); #303 Root Orchestrator digest reusing `GET /system-state` as
    the canonical fact source (severity routing, deterministic dedupe,
    progressive disclosure, Ask decisions flow back to Goal/Task, proposal
    accept ≠ execution approval); #302 quality sampling (separate from SDK
    lineage sampling), auditor-model separation, blind re-audit, quality
    floor circuit breaker per Cell, System-scoped cost caps; #304
    improvement lifecycle (`observed → … → adopted|rejected|blocked`) with
    canary evidence from existing Replay/offline-shadow/Experiment infra,
    parent + human approval gates, shadow proposal vs live-shadow execution
    approval as separate records, and no bypass of the existing #25/#216/
    #242/#252 human gates. Keep the three structures separate: System
    Topology Graph (existing, referenced read-only), Goal/Accountability
    Tree, and Cell Runtime State. See the Issue #297 section in
    `docs/project-intelligence.md`.
    **Status: #297 and #298-#304 are implemented, verified, and closed.**
    #314 additionally closes the final operational-proof gap with a 3-Cell
    end-to-end read-only pilot fixture: one approved Feature/Probe Plan binds
    three worker Cells, state/domain/root digest reads expose
    Feature/API/Trace/evidence drill-down, the same Cell ids remain isolated
    across Systems, and the GET-only pilot is verified not to call an LLM or
    mutate the target repository, component policy, or Cell persistence.

13. Issue #307 — remaining work after #282 / #295 / #297 closed. Three
    categories, all as sub-issues: (A) deliberate deferrals already documented
    — #308 Inquiry premise tracking + `superseded` (**implemented and
    verified**, via sub-issues #320 premise bundle / #321 review subject
    identity + lineage / #323 premise evaluation + `superseded` transition /
    #322 Dashboard; premise tracking is `origin_kind='review_item'` only and
    never guesses a successor, and `superseded` is terminal and system-only),
    #309 the §9 evaluation
    metrics pipeline (**implemented and verified**), #310 sample-error driven
    rule re-evaluation, #311 低リスク提案の一括承認 (the measurement
    mechanism now exists, but implementation remains blocked until a real
    observation cohort and undo/misclassification cases exist);
    (B) gaps found during the closing review and not previously documented —
    #312 the §5.5 cascade beyond goal/Intent (**implemented and verified**),
    #313 externalizing the
    no-review-required policy (**implemented and verified**), and #314 the 2〜5 Cell read-only pilot fixture
    (**implemented and verified with a 3-Cell E2E fixture**); (C) #315
    contract/test hardening. None of these relax an existing human gate. Do
    not start #311 before #309 lands.

14. Issue #328 — 共同理解 Epic: treat 「わからない」 as the START of a joint
    investigation instead of a terminal answer. The developer and the agent
    take turns — investigate the pinned snapshot, translate findings into
    purpose/impact, update hypotheses, and only then ask the human what only
    a human can decide. Three provenances are never merged into one answer:
    system investigation (technical facts + evidence), translation (meaning
    for the developer's goal, invents no facts), and the developer (goals,
    tradeoffs, final judgement). Implement sub-issues in dependency order:
    - #329 (Phase A) — the deterministic exchange contract and dialogue
      state: `shared/schemas/joint_understanding.schema.json`,
      `app/joint_understanding.py`'s finite vocabularies (`origin_role` ×
      `claim_kind`, session transitions, outcomes, action kinds),
      `joint_understanding_session/_finding/_action` tables, and
      `routes/joint_understanding.py`. Findings are append-only (corrections
      carry `supersedes_finding_id`); a translation may not carry evidence
      and must reference findings of the SAME session; a developer finding is
      always `manual` and never carries an intelligence run.
      `outcome='hypothesis_adopted'` is explicitly provisional and never a
      fact. **No endpoint writes the origin `interview_qa` /
      `interview_intent_item` / `alignment_item` / `interview_inquiry` row —
      not even its status** (unlike #287's Inquiry mirroring), and
      「わからない」 never becomes a developer finding.
    - #330 (Phase B) — `app/investigation_loop.py`: iterative rounds over the
      pinned snapshot with carried-over `search_leads` / `open_hypotheses` /
      `missing_evidence` / `read_paths` (restored on retry, so a re-run
      resumes instead of restarting), cross-source candidate retrieval
      (symbol index, entrypoint index, file CONTENT scan, plus #286's path
      names), finite stop reasons (`answered` | `budget_exhausted` |
      `no_new_evidence` | `unresolved` | `failed`), and one
      `intelligence_runs` row per round. A round-1 failure yields no
      findings; a later-round failure keeps the earlier validated ones.
      `investigate()` (#286) is reused, never replaced.
    - #331 (Phase C) — `app/understanding_translator.py`: findings-only
      input (no snapshot excerpts, so it cannot invent facts), mandatory
      `supports_finding_ids` on every sentence (an unsupplied id fails the
      WHOLE call closed), claim kinds restricted to inference/unknown/
      conflict, and a deterministic action menu from the fixed server
      catalog. `ask_developer` refuses to hand a question back when the
      developer has no decision material and investigation could still help.
    - #332 (Phase D) — reflux: only investigation FACTS attach to the
      understanding surface, always `decision_method: reasoning_llm`, into
      `interview_qa.investigation_json` (the same non-answer slot #286 uses)
      or the session ledger; a stale premise (session moved to a newer
      snapshot) blocks reflux and both asserting outcomes, and
      `hypothesis_adopted`/`decided` must name their basis findings.
    - #333 (Phase E) — Dashboard 共同理解 panel: 4-layer disclosure, finite
      action menu, provisional-vs-decided distinction, entry point from the
      Q&A card's 「わからない」 flow (the #142/#295 flow is unchanged).
    - #334 (Phase F) — 8 quality metrics in their own `joint_understanding`
      category on the #309 pipeline, deterministic, `unmeasured` when there
      is nothing to measure, never merged with the efficiency numbers.
    **Status (2026-07-31 audit): #329-#334 are implemented and hardened, but
    their original intent is not complete in the production flow.** The
    deterministic gaps that could be fixed locally (evidence/provenance
    validation, resumable round numbering, grounded translation, cumulative
    reflux, action audit/resume, and metric supersession) are covered here.
    The remaining product/architecture decisions were split into #336
    (single production flow), #337 (premise/provenance/decision audit), #338
    (outcome-lineage quality metrics), and #339 (investigation breadth and
    Question Router integration). #328-#334 were then closed in favor of
    those narrower follow-ups.
    #327 was closed `not_planned` and fully absorbed into this Epic; its
    design notes remain valid in `docs/system-understanding-ideal-state.md`.
    #311 (低リスク提案の一括承認) is still NOT implemented and is not a
    prerequisite of this Epic. See the Issue #328 section in
    `docs/project-intelligence.md`.

15. Issue #342 — システムインタビューを状態駆動型の開発者ワークフローへ
    再設計する (sub-issues #343-#346). This is a **UX specification** issue:
    every sub-issue lists Dashboard component changes, API/DB/state-management
    design, and test implementation as 対象外, so the deliverable is the spec,
    not code. The spec lives in `docs/system-interview-workflow-ux.md` and
    defines: developer-facing states `W0-A`/`W0-B`/`W1`-`W7` decided by a
    **two-stage evaluation** — a first-match rule table over persisted facts,
    then a backward-transition hold (#343); information roles `R1`-`R6`, one
    role per element, with an inventory of the screen's 70 main UI elements
    and a state × role display matrix (#344); a finite automation gate
    `A1`-`A4` — does not touch the target repo, does not change approval
    state, failure cannot break existing confirmations, result is recorded
    and awaits a human — plus exactly one primary action per state, which
    must be the operation that satisfies that state's completion condition
    (#345); and exceptions `E1`-`E14` split into blocking / degraded /
    informational with 8 walkthrough scenarios (#346). Degraded is decided by
    whether enough material remains to continue the current decision, not by
    which process failed — hence `E3-b` can continue through zero-base questions,
    while `E4-b` can continue from a surviving earlier result. Forward
    transitions are automatic;
    **backward transitions into an already completed state always require
    explicit confirmation**, which the rule table alone cannot express — the
    hold needs a persisted `reached_state` workflow checkpoint + per-request
    acknowledgement, and
    the hold applies only to the ordered states (`W2`-`W7`) — `W0-A`/`W0-B`/
    `W1` carry no workflow position, so a normal `W3`→`W1`→`W4` is never
    mistaken for a backward move. The
    spec relaxes no human gate: 理解の確認 / Alignment 項目の確定 /
    提案の承認・編集・却下 / 差分の適用 / 観測の開始 all stay
    `decision_method: manual`, and the isolated-worktree boundary is
    unchanged. It builds on #341 (the metrics panel keeps its progressive
    disclosure and its `guardrail`-vs-要確認 separation; the spec only moves
    it out of the main flow and fixes it as `R6`). Implementing it is
    deliberately out of scope here — when a follow-up implementation issue is
    written, follow §8 of the spec: it requires exactly four new persisted
    facts — 差分レビューの完了 for `W6`→`W7` (manual); an execution record for
    every process that can produce `W1`, which doubles as the blocking-failure
    record carrying the state it blocks (system); `reached_state` + backward
    requests (system); and the acknowledgement of a backward request (manual).
    `reached_state` is the current ordered checkpoint, not an all-time monotonic
    maximum: it moves backward only with that manual acknowledgement. A safe
    terminal exit uses the existing session `status=closed` ahead of blocking
    failures, does not advance `reached_state`, and is itself audited as manual;
    reopening restores `status=open` and resurfaces unresolved failures.
    Everything else is derivable from
    existing persisted facts. See the Issue #342 section in
    `docs/project-intelligence.md`.
    **Status: the spec is implemented by Issue #349 (below). Treat
    `docs/system-interview-workflow-ux.md` as the canonical description of
    the interview screen's behaviour, not as a future plan.**

16. Issue #349 — the implementation of the #342 spec. Unlike #343-#346, this
    issue explicitly OWNS code: DB, API, state management, Dashboard, tests.
    What it added, and what any later change must preserve:
    - `app/interview_workflow.py` is the **single canonical state engine**.
      `evaluate_candidate_state` is the 13-row first-match table and
      `apply_backward_hold` is the ordered-state hold; both are pure
      functions of a `WorkflowFacts` value. Do not re-derive a workflow
      state anywhere else — in particular never in the Dashboard.
    - The four §8.1 facts are five tables: `interview_diff_review` (A,
      manual, keyed to the reviewed diff's `materialized_at` so a new diff
      never inherits the old confirmation), `interview_process_run` (B,
      running/succeeded/failed + `failure_class` + a `W2`/`W4`/`W5`
      `target_state`), `interview_workflow_checkpoint` +
      `interview_back_request` (C), and `interview_back_acknowledgement`
      (D, manual). `interview_session_status_audit` records `OP-D14`
      suspend/resume as manual; the state itself still comes from the
      existing session `status`.
    - "Unresolved" for a blocking failure is **derived** (no later success
      of the same `process_kind`), never stored — so a suspend/resume cannot
      launder a failure into a solved one.
    - Every process that can produce `W1` must open a run record:
      `interview_workflow.process_run` / `ProcessRunTracker` /
      `tracked_process`. A 404/409 precondition rejection is `abandon()`,
      not a failure — recording it would show a retry that cannot succeed.
      A process without a run record must not produce `W1`.
    - `GET /interview/workflow-state` is the only source of the displayed
      state, its single `primary_action`, and the active exceptions. The
      Dashboard renders that; `deriveUiState` survives only as an internal
      selector for the conversation card's CONTENT and no longer takes a
      mutation's `isPending`.
    - The spec's abolished elements are gone from the page: the two main
      tabs and the 「会話タブへ移動」 lead, the 「差分を生成」 button (`OP-S7`
      is automatic), the three "precondition not met" notes, and the Intent
      「AIに提案してもらう」 button. 「理解を構築/更新」, 「突き合わせを実行」,
      「AIに先に調査させる」 and 「実態チェックを実行」 exist only as the
      recovery action of a currently-active exception.
    - `W6`'s primary action is the diff-review record itself; downloading
      the patch must never satisfy it.
    Review round 1 (PR #350) added five more invariants, each of which was a
    stuck screen rather than a cosmetic slip:
    - Every Dashboard mutation that changes a state-deciding fact calls
      `_invalidateWorkflow`. The page renders one server-decided state and
      the workflow query only polls while something is running, so a missed
      invalidation freezes the flow until a reload.
    - `POST /interview/sessions` opens the initial build's run record itself
      (`W0-B` completes by "session created AND investigation started"); the
      request that performs the build adopts that record. Otherwise a reload
      between the two calls drops a new session into `W7` permanently.
    - `open_required_questions` excludes questions held by an in-flight
      handoff — 「引き継ぎ済み」 is a `W3` completion condition and the
      `interview_qa.status` column deliberately cannot express it.
    - Failure resolution compares `finished_at` (overlapping runs of one kind
      can finish out of id order), and the stale sweep is provisional: a real
      completion still overwrites it, because nothing writes `heartbeat_at`
      mid-run.
    - A suspended session never auto-resolves a pending backward request, and
      resuming records the manual acknowledgement for it (§5.4 terminal 3).
    Review round 2 (PR #350) tightened four more, all of them "the developer
    cannot get out of here" rather than a wrong label:
    - Auto-selecting a session happens once, on first load, and never
      overrides an explicit 「セッション未選択」. Re-selecting on every render
      made `W0-B` unreachable on any System that already had a session, i.e.
      no second interview could ever be started.
    - `POST /interview/sessions` does not merely reserve the run record — it
      **dispatches the build**, adopting that exact record (eager inline when
      `PROBE_INTERVIEW_EAGER_INITIAL_BUILD=1`, otherwise a worker thread; a
      dispatch that cannot start is failed immediately as a recoverable
      `E3-a` rather than waiting for the stale sweep). Consequently the
      Dashboard's start flow no longer posts `update-understanding` — that
      second call would run the same reasoning build twice.
    - `ProcessRunTracker.start(adopt_run_id=...)` adopts ONE named record.
      Adopting "the oldest running row of this kind" let two legitimately
      overlapping rebuilds share a row, so whichever finished last decided
      for both and could erase the other's failure.
    - Failure resolution reads the LATEST finished run per `process_kind`
      (`finished_at`, id as tiebreak), not "any later success". With three or
      more overlapping runs, an older success would otherwise mask the newest
      failure.
    Tests must not spawn that initial-build worker: `tests/conftest.py` stubs
    `_dispatch_initial_understanding_build` for every test, and
    `test_interview_workflow.py`'s `real_initial_build_dispatch` fixture puts
    it back for the two tests that assert on it.
    Human gates are unchanged: 理解の確認 / Alignment 項目の確定 / 提案の
    承認・編集・却下 / 差分の適用 / 観測の開始 / 戻り要求への承諾 / 中断・
    引き継ぎ・再開 all stay `decision_method: manual`.

17. Issue #351 (subs #352-#354) — the Understanding Brief and Decision
    Readiness: the layer BEFORE #349's workflow position. A developer opening
    the interview screen must be able to say, within 5 seconds, what the AI
    thinks the system is for, which parts are settled, and whether the current
    understanding can be used to proceed. What it added, and what any later
    change must preserve:
    - **Vision is a claim of its own**, not a flavour of System Purpose.
      `system_understanding_reviewer` gained a `vision` section
      (`understanding-review-v6` / schema `understanding-review-v2`), capped
      at one item. It is deliberately NOT in `_EVIDENCE_REQUIRED_SECTIONS`;
      instead an evidence-less Vision is deterministically clamped to
      `uncertain`, so it can only ever render as a hypothesis. The prompt
      requires an empty list rather than a Vision reverse-engineered from the
      code's mechanics. On the Brief, a confirmed Intent Brief `goal`
      outranks the model's Vision.
    - **確認状態 and 出所 are two independent finite axes**
      (`app/understanding_brief.py`). `classify_provenance` never looks at
      whether a human pressed 確認 — approving an AI-written sentence does
      not make the developer its author. `classify_confirmation` is
      first-match with conflict > post-confirmation change > confirmed, so a
      confirmed claim the system later changed can never keep reading
      「確認済み」.
    - Claim identity is exact name equality (same rule as
      `understanding_diff`); content change is a `claim_digest` comparison
      over the meaning-bearing fields, name excluded. No similarity, no
      embeddings. The confirmed baseline is #312's
      `understanding_capability_confirmation.source_revision_id`, falling
      back to the newest revision at or before `understanding_confirmed_at`.
    - **Decision Readiness never gates and never re-decides the workflow
      state.** `evaluate_readiness` returns one of `not_built` / `building` /
      `blocked` / `recheck_required` / `needs_confirmation` / `ready` plus
      finite reason codes carrying a severity (`blocking` / `attention` /
      `informational`) and a target. The single primary action still comes
      from `app/interview_workflow.py` and stays visible under 進行不可.
      No composite confidence percentage anywhere.
    - Only `BRIEF_AFFECTING_PROCESS_KINDS` (`understanding_build` /
      `understanding_update` / `intent_candidates`) may move the verdict, for
      running records and blocking failures alike. A running proposal
      generation is not 「理解を作成しています」 and a failed diff generation
      is not 「理解を作る処理が失敗した」 — that conflation is the thing #353
      forbids. The Dashboard's `W1` heading follows the server's
      `readiness_state` for the same reason.
    - Zero Core Capabilities is never `ready` (`capabilities_missing`,
      attention): #352 requires that a Purpose-only understanding does not
      read as complete. It does not block — Purpose alone is still judgeable.
    - `claim_payload` is the single definition of a claim's content. The
      digest that decides a recheck and the change list the developer reads
      are both derived from it, so a claim can never be reported as changed
      without the change being nameable. Additions/removals and detail
      changes are listed together.
    - The Brief's six finite vocabularies are `Literal` aliases in
      `app/models.py`, mirrored into `understanding_brief.py` with `get_args`
      and held to the Dashboard unions by `test_interview_type_parity.py`'s
      `FINITE_TYPE_NAMES`. A bare `str` API field puts no enum in the schema
      and lets the TypeScript union drift unnoticed.
    - `GET /interview/understanding-brief` is the only source of the Brief;
      the Dashboard renders it and never recomputes a confirmation state,
      provenance, or readiness verdict. Only the display density is a client
      table (`BRIEF_DISPLAY_BY_STATE`: `W0`→empty, `W1`→building, `W2`→full,
      `W3`-`W7`→compact).
    - The Brief lives at the top of the MAIN column in every state, because
      the right column wraps below the main work on narrow screens. The
      right-column 「現在の理解」 card and its `last_error` block were removed
      as duplicates (P7); the full tree survives inside the Brief's
      disclosure, and `W2`'s 「この理解で進む」 moved into the Brief card so
      the judgement target and the primary action share one surface (P2).
    - A Brief that cannot be fetched degrades to `unavailable` but KEEPS the
      primary action and the full tree. The summary assists the decision; it
      is not a precondition of the workflow.
    Human gates are unchanged; this epic writes nothing at all.

The Repository, Feature Map, Probe Planner, and Experiments tabs are no
longer whole-page mocks: they call real Control Server endpoints, and
`is_mock` badges mark mock LLM output per response (provenance labeling,
not page status). The old `GET /project-intelligence` mock endpoint has
been removed. The rule that remains: do not silently present mock LLM
data as persisted or analyzed data — mark it visibly wherever it appears.

A conversational system-understanding interview that proposes `probe-agent:`
docstring metadata and probe instrumentation together (see Principle 8) is a
future phase building on #25's isolated-worktree instrumentation. It is not
owned by #23-#26 as written; do not start implementing it until it has its
own issue, so persistence and approval flows are designed deliberately
instead of being bolted onto an unrelated issue's scope.

---

## Core Design Principles

1. Safety first.
   - Default behavior must preserve the original function behavior.
   - If the Control Server is unavailable, the original function must run normally.
   - `replace` mode is out of scope for MVP.
   - `shadow` mode must never affect the returned production value.

2. Probe must be lightweight.
   - Minimize overhead.
   - Avoid blocking the target function whenever possible.
   - Never make tracing failures break the target application.

3. Schemas are contracts.
   - `TraceEvent`, `ControlPolicy`, and `ShadowResult` must remain consistent across SDK, server, dashboard, and examples.
   - Schema changes must update shared schemas, server models, SDK types, tests, and docs together.

4. Start with pure-ish components.
   - The MVP should target functions such as summarize, classify, normalize, extract, retrieve.
   - Avoid payment, email sending, DB writes, irreversible side effects, and authentication logic as shadow targets.

5. Read target repositories from Git, not the working tree.
   - Pin a commit SHA before analysis.
   - Enumerate with `git ls-files` and read with `git show <sha>:<path>`.
   - Never read untracked, ignored, or uncommitted file contents.
   - Reject path traversal and repository-external symlink access.
   - Do not write to the target repository's tracked branches, and never
     commit or push to it directly. The one exception is the conversational
     metadata/probe authoring flow in Principle 8, which writes only inside
     an isolated worktree and stops at a reviewable diff/PR — the developer
     performs the final apply/merge into the real repository.
   - Issue #216's GitHub App publish workflow extends this same exception to
     a remote repository: only after an explicit human approval may
     probe-agent, using a short-lived GitHub App Installation Token, commit
     and push to a server-generated `probe/`-prefixed branch and open a Pull
     Request. Direct pushes to the default/base branch, force pushes, and
     any push without prior human approval remain forbidden. The developer
     always performs the merge on GitHub — probe-agent never merges or
     closes the PR itself.

6. Limit deterministic decisions to explicit finite sets.
   - Deterministic rules are allowed only when the result belongs to a small,
     explicitly enumerated set or is direct structural validation.
   - Examples: file kind, known decorator presence, status transitions, exit
     code success/failure, schema validation, exact safety denylist matches.
   - System understanding, Feature extraction, Feature-to-Code mapping, probe
     selection, unknown side-effect analysis, and experiment interpretation
     require an external reasoning-model LLM API.
   - Keyword scores, similarity, embeddings, and static matches may retrieve
     candidates, but must not become the final open-ended decision.
   - If reasoning-model configuration, API calls, or structured-output
     validation fail, fail the run. Never fall back to heuristic inference.

7. Keep reasoning auditable.
   - Persist provider, model, prompt version, schema version, decision method,
     source snapshot, timestamps, and failure details for every intelligence run.
   - Decision method must be one of `deterministic`, `reasoning_llm`, or `manual`.
   - Mock LLM output is test/local-smoke data and must be visibly marked as mock.
   - LLM recommendations never directly approve, adopt, merge, or deploy changes.

8. Isolate all source changes and execution.
   - Instrumentation and source variants run in temporary worktrees/workspaces.
   - Commands must come from explicit repository configuration.
   - Network is off by default; environment variables are allowlisted.
   - Preserve reviewable patches and deterministic raw results.
   - A conversational system-understanding interview (purpose / capability /
     element discovery with the developer) may propose `probe-agent:`
     docstring metadata and probe instrumentation together. Proposed text is
     LLM-authored and stays `reasoning_llm` until a human approves it; once
     approved, probe-agent materializes the docstring edits and probe
     instrumentation in the same isolated worktree used for Probe Plans
     (Issue #25) and produces a single reviewable diff or pull request.
   - probe-agent never commits or pushes the result to the target
     repository's tracked branches itself. The developer reviews the diff/PR
     and performs the merge — this keeps the "no direct write to target
     repo" boundary (Principle 5) while removing manual transcription work.
   - Issue #216's GitHub App publish workflow is the sanctioned way to turn
     an approved isolated-worktree patch into a real commit/push/PR against
     a remote repository: approval gates the push, the push target is always
     a server-generated `probe/`-prefixed branch (never default/base, never
     force-pushed), and Installation Tokens are short-lived and never
     persisted. This does not relax the isolation rule above — instrumentation
     and source variants still only ever run and get patched inside temporary
     worktrees/workspaces.
   - This does not relax Principle 6: which capability an element belongs to,
     what its role/probe value is, and what to instrument remain
     reasoning-model proposals, never heuristic free-text guesses, and the
     developer's approval is the `decision_method: manual` record, not the
     LLM's output alone (Principle 7).

---

## Required Workflow Before Code Changes

Before modifying code, always check whether the requested change requires updates to:

- `CLAUDE.md`
- `.claude/skills/*/SKILL.md`
- shared schemas
- docs
- tests
- example app

If any instruction, workflow, schema rule, or recurring implementation pattern changes, update the relevant `CLAUDE.md` or `SKILL.md` first, then proceed with the implementation.

For issues #23-#26, always load:

- `.claude/skills/project-intelligence/SKILL.md`
- `.claude/skills/reasoning-llm/SKILL.md` when any non-finite inference is involved
- the area-specific skills for Control Server, Dashboard, schema, and testing

Read the owning GitHub issue and `docs/project-intelligence.md` before coding.
Treat later issues as non-goals unless the current issue explicitly expands scope.

If the change affects behavior, add or update tests unless there is a clear reason not to. If tests are not added, explain why.

---

## Testing Policy

Use tests to protect the expected behavior of the MVP.

Required test coverage:

- `@probe` preserves original return values
- `@probe` preserves original exceptions
- tracing failure does not break the wrapped function
- environment variable can disable the probe
- policy `off` skips tracing/control behavior
- policy `trace` records input/output/error/duration
- policy `shadow` returns current output while recording candidate output
- schema changes are validated against examples
- repository snapshots exclude uncommitted and untracked contents
- evidence locations resolve against the pinned snapshot
- reasoning-required operations do not use heuristic fallback
- reasoning run metadata and failures are persisted
- target repositories remain unchanged after worktree/experiment operations
- deterministic raw metrics remain available when interpretation fails

Do not rely only on manual testing when behavior can be covered by unit tests.

---

## Implementation Constraints

- Prefer small, focused changes.
- Keep interfaces explicit.
- Use typed models where reasonable.
- Avoid remote arbitrary code execution.
- Avoid hidden mutation of inputs and outputs in MVP.
- Do not introduce production replacement behavior unless explicitly requested in a future phase.
- Document any new environment variables.
- Update examples when public usage changes.
- Do not add speculative DB tables for later roadmap phases.
- Add persistence in the issue that owns the lifecycle and query requirements.
- Prefer additive SQLite schema changes; include migration/backfill behavior and
  isolation tests for every System-scoped table.
- Never hold a `db.get_conn()` connection across an external call (LLM round
  trip, subprocess). Its lock is process-wide and non-reentrant, and every LLM
  client opens its own connection to consume System quota — so an LLM call
  inside `with get_conn()` deadlocks the entire server until it is restarted.
  Structure such endpoints as read → reason → persist with the connection
  closed during the reasoning call. See `.claude/skills/control-server/SKILL.md`.
- Keep raw deterministic facts separate from LLM interpretations in storage.

---

## Dashboard UI言語規約 (Issue #266)

Server-supplied state strings are already unified to Japanese via
`apps/control-server/app/state_messages.py` (#240). This rule extends the
same convention to dashboard-side hardcoded UI copy, so a screen never mixes
English and Japanese within the same CTA group, heading, empty state, or
toast.

- User-visible UI copy in `apps/dashboard/src` (buttons, headings, empty
  states, toasts, dialogs, placeholders): Japanese.
- Technical identifiers and proper nouns stay in their canonical form and are
  never translated: System, Trace, Replay, Experiment, Snapshot, Capability,
  policy modes (`off` / `trace` / `shadow`), GitHub, PR, branch names, HTTP
  status codes, env var names. Established product-concept names (e.g.
  Capability Map, Flow Explorer, AI Candidate Studio) may remain as-is when
  they ARE the concept, following the 初出のみ併記 style already used in
  `docs/system-understanding-navigation.md`'s terminology table (English term
  once at first mention, Japanese prose around it).
- `state_messages.py`-supplied server strings remain the canonical source of
  truth; any client-side fallback string (used only when a server field is
  absent, e.g. an older Control Server) must also be Japanese, never an
  English default.
- No i18n framework and no language switcher are introduced by this rule —
  it is a plain-literal convention, enforced by review and by keeping
  text-matching tests in sync with the copy they assert on.
- Exhaustive translation of every minor label in one pass is not required;
  the binding acceptance bar is that no single screen/CTA group mixes
  languages. See `.claude/skills/dashboard/SKILL.md` for the operational
  detail (constant-coupling caveats, shared-component scope, etc).

---

## Verification Checklist

Before finishing a task, run the relevant checks when available:

- Python tests for modified packages
- Type or lint checks if configured
- Example app smoke test if SDK/server behavior changed
- Manual verification notes for dashboard-only changes

Summarize what was changed, what was tested, and any remaining risks.
