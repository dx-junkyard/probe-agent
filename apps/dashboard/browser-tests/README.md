# Browser tests (Issues #384, #427/#432)

`vitest` runs in jsdom, which has no layout, no clock-driven refetch and no
real navigation. Three of #384's acceptance conditions cannot be verified there
at all, so they are verified here against the real Control Server and the built
SPA in Chromium.

## What is covered

| Scenario | Why it needs a real browser |
| --- | --- |
| `receiving_now → delayed → stale → receiving_now` | The transition is driven by the SERVER clock and the page's own refetch schedule. Nothing is reloaded and nothing is clicked. |
| Experiment deep link + reload | URL-driven selection, a real reload, and the fail-safe that refuses to expand an already-decided experiment. |
| Degraded Brief | The real render path for a partial failure: other sections survive, no CTA is guessed, and an unreadable baseline is not reported as 未確認. |

Scenario 1 uses the real server end to end. Scenarios 2 and 3 control the
RESPONSE by route interception — what they test is the browser's own behaviour
(router, reload, render), not the server's projection, which the pytest suite
already covers.

## Objective Map (`objective-map.cjs`, Issue #427/#432 review §4.1)

Six guarantees of the Objective Map screen that jsdom cannot check, each one a
way the screen fails while its unit tests stay green:

| Scenario | Why it needs a real browser |
| --- | --- |
| Nested deep link + reload | A link to a CHILD Objective is only visible if its ancestor is force-opened. jsdom has no real navigation or reload to lose the selection in. |
| An unreadable Gap read is not `0 件` | The server returns an all-zero summary and signals the failure only in `degraded_sections`, so the zeros are indistinguishable from a real "no Gaps" unless the reader checks. Verified on the shape the server actually sends. |
| back/forward re-syncs selection AND filters | Selection uses `replace: true` (a tree click is not a navigation), so this drives the URL directly. The Gap filter is the half that used to drift: it was seeded from the URL on first render only. |
| One lane pending does not block the other | The Gap panel mounts only when its tab is active, and a short delay would race the fetch and pass for the wrong reason — so the delay is 20s and the tab is opened first. |
| WAI-ARIA tabs | Roles, `aria-selected`, `aria-controls`, and arrow-key roving focus need a real focus model. |
| 390px viewport | Needs real layout to detect horizontal overflow. |

Auth and System creation run against the real server; only the two projections
are intercepted, for the reason given above.

```bash
NODE_PATH=/tmp/pw/node_modules node browser-tests/objective-map.cjs /tmp/out
```

## Running

Playwright is deliberately not a repository dependency (the same treatment
Issue #358's measurement harness got): it is a verification tool, not something
the dashboard build needs.

```bash
# 1. Playwright, outside the repo.
npm i --prefix /tmp/pw playwright-core

# 2. The Control Server, on a scratch database.
cd apps/control-server
PROBE_DB_PATH=/tmp/e2e/probe.db \
  CONTROL_ADMIN_USERNAME=root CONTROL_ADMIN_PASSWORD=s3cret LLM_PROVIDER=mock \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8099 &

# 3. The built SPA plus an /api proxy, so cookies and same-origin match
#    production.
cd apps/dashboard
npm run build
node browser-tests/serve.cjs dist 8099 8098 &

# 4. The scenarios.
NODE_PATH=/tmp/pw/node_modules node browser-tests/overview-freshness.cjs /tmp/e2e
```

`CHROMIUM_PATH` overrides the browser binary (it defaults to this image's
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`). Screenshots are written
to the output directory passed as the last argument. The script exits non-zero
and prints every failed expectation.

The fixture narrows the System's freshness thresholds to 6s / 14s
(`PUT /connectivity/freshness-policy`) so the transition happens inside a test
rather than in a day.

## What this found

The first run of scenario 1 caught a real defect: the Overview's
`refetchOnWindowFocus` / `refetchOnReconnect` were inert, because the app-wide
`staleTime: 30_000` in `src/main.tsx` suppresses a focus refetch while the data
is still within it. The interval-driven transitions worked (`refetchInterval`
ignores `staleTime`), so only a real browser returning to a real tab could show
it. `useOverview` now sets `staleTime: 0`.
