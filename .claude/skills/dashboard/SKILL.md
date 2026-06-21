---
description: Use when implementing or modifying the dashboard for viewing traces, policies, and shadow comparisons.
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

## Verification

For UI-only changes, provide manual verification steps if automated tests are not available.
