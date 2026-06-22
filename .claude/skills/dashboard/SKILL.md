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
