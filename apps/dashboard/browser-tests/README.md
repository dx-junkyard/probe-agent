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

Ten guarantees of the Objective Map screen (and the CTA destination it hands
off to) that jsdom cannot check, each one a way the screen fails while its
unit tests stay green:

| Scenario | Why it needs a real browser |
| --- | --- |
| Nested deep link + reload | A link to a CHILD Objective is only visible if its ancestor is force-opened. jsdom has no real navigation or reload to lose the selection in. |
| An unreadable Gap read is not `0 件` | The server returns an all-zero summary and signals the failure only in `degraded_sections`, so the zeros are indistinguishable from a real "no Gaps" unless the reader checks. Verified on the shape the server actually sends. |
| back/forward re-syncs selection AND filters | Selection uses `replace: true` (a tree click is not a navigation), so this drives the URL directly. The Gap filter is the half that used to drift: it was seeded from the URL on first render only. |
| One lane pending does not block the other | The Gap panel mounts only when its tab is active, and a short delay would race the fetch and pass for the wrong reason — so the delay is 20s and the tab is opened first. |
| WAI-ARIA tabs | Roles, `aria-selected`, `aria-controls`, and arrow-key roving focus need a real focus model. |
| 390px viewport | Needs real layout to detect horizontal overflow. |
| Milestone-only deep link reaches the WORK pane | The tree already reveals such a Milestone, so a "is it in the DOM" check passes either way. What fails without normalization is the pane the link exists for -- `objectiveKey` stays null, so `MilestoneWorkPanel` never mounts -- and asserting that the URL is CORRECTED needs a real history stack. |
| Form state never travels between entities, and is not discarded silently | The wrong-entity write: text typed for Objective A still on screen after selecting B, and 「記録する」 saving it as B's revision. The remount, the refetch and the controlled inputs only interact for real in a browser — and the discard prompt is a `window.confirm`, which exists nowhere else. |
| A Milestone under another Objective moves the Objective selection | The pane is keyed off `objectiveKey`; without normalization it keeps describing the previously selected Objective while the Milestone panel underneath acts on a different one's. Needs a real expand, click and URL rewrite. |
| Requirement → Feature completes by explicit submit | Review 0.3 asks for proof the operation can be FINISHED, not that a screen opened. Drives the Overview CTA's own URL (target included) and asserts the recorded POST — and that selecting alone records nothing. |

Auth and System creation run against the real server; the two projections plus
the Objective/Milestone/Gap detail reads are intercepted, for the reason given
above. Scenarios 7-8 need those detail responses to exist and to DIFFER per
entity -- that difference is exactly what a carried-over form would hide.

```bash
NODE_PATH=/tmp/pw/node_modules node browser-tests/objective-map.cjs /tmp/out
```

## Dashboard 横断 UX 監査 (`ux-audit-466.cjs`, Issue #466)

Issue #466 の 20 件のうち、jsdom では確かめられない保証を実ブラウザーで
確認する (91 項目)。サーバーは実物を使い、失敗 (500・通信断・401) だけを
route interception で注入する。

| Scenario | Why it needs a real browser |
| --- | --- |
| 対象付き URL → ログイン → 同じ URL (UX-09) | 実際の redirect と query/hash の保持。外部 URL の `next` を無視すること |
| 同名 token の識別と失効確認 (UX-01) | 実サーバーの 2 System × 同名 token |
| コピー拒否 / 許可 (UX-08) | クリップボード権限と `getSelection()` の実挙動 |
| 共通 Dialog のフォーカス (UX-02) | 初期フォーカス・Tab 循環・Escape・復帰・スクロールロック |
| ヘッダーの重なり (UX-14) | 320/390/768/1024/1280px と 1280px + アシスタントで、表示中の操作要素どうしの矩形の重なりを測る。横 overflow だけでは衝突を見逃す |
| Workspace の URL (UX-10) | 再読込・戻る・進む |
| 下書きの保持 (UX-03/04) | 背景クリック、タブ往復、離脱時の `confirm` |
| 取得失敗の表示 (UX-05/06/12) | 500・通信断・401 を注入し、0 件や未ログインとして描かないこと。401 の再認証後も入力が残ること |
| Setup Guide / ナビ (UX-11/13/19) | 画面移動をまたぐ保存、検索からの遷移、減光なし |
| アシスタント (UX-16/17/20) | 1280px で本文と並ぶ・390px でモーダルへ戻る、Shift+Enter、CDP の IME 合成中 Enter |

スクリーンリーダーの読み上げ・実際の日本語 IME・代表利用者の評価は
ここでは確認しない (Issue #463)。

```bash
NODE_PATH=/tmp/pw/node_modules node browser-tests/ux-audit-466.cjs /tmp/out
```

`E2E_USER` / `E2E_PASS` が管理者資格情報 (既定は下の手順の `root` / `s3cret`)。
固定の名前で System・token・Workspace を作るので、毎回新しい scratch DB で
実行する。

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
