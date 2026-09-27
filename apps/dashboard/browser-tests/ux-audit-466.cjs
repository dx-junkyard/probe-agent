// Issue #466 acceptance in a REAL browser.
//
// jsdom has no layout, no real focus model, no history stack, no clipboard
// permission model and no IME. The guarantees below are therefore checked
// against the built SPA and the real Control Server in Chromium. Responses are
// intercepted only where a FAILURE has to be injected (500 / offline / 401):
// what is under test there is how the browser presents the failure.
//
// This does NOT verify screen-reader output: live-region and dialog roles are
// asserted from Chromium's accessibility tree, which is the input a screen
// reader consumes, not what it speaks. Real screen-reader and representative-
// user evaluation is tracked in Issue #463.
//
//   npm i --prefix /tmp/pw playwright-core
//   NODE_PATH=/tmp/pw/node_modules node browser-tests/ux-audit-466.cjs /tmp/out
//
// See browser-tests/README.md for starting the server and the SPA.
const { chromium } = require("playwright-core");
const fs = require("node:fs");

const CHROMIUM =
  process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const APP = process.env.APP_URL ?? "http://127.0.0.1:8098";
const OUT = process.argv[2] ?? "/tmp/ux-audit-466";
const USER = process.env.E2E_USER ?? "root";
const PASS = process.env.E2E_PASS ?? "s3cret";

fs.mkdirSync(OUT, { recursive: true });

const failures = [];
function expect(label, actual, wanted) {
  const ok = JSON.stringify(actual) === JSON.stringify(wanted);
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${label}: ${JSON.stringify(actual)}`);
  if (!ok) failures.push(`${label}: got ${JSON.stringify(actual)}, want ${JSON.stringify(wanted)}`);
}
function expectTrue(label, actual) {
  console.log(`  ${actual ? "ok  " : "FAIL"}  ${label}: ${actual}`);
  if (!actual) failures.push(`${label}: expected true`);
}

async function login(page) {
  await page.goto(`${APP}/login`);
  await page.getByLabel("Username").fill(USER);
  await page.getByLabel("Password").fill(PASS);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
}

// Same-origin fetch from inside the app, so the session cookie and the CSRF
// double-submit cookie are the real ones.
async function api(page, method, path, body, systemId) {
  return page.evaluate(async ({ method, path, body, systemId }) => {
    const csrf = document.cookie.split(";").map((c) => c.trim()).find((c) => c.startsWith("probe_csrf="));
    const headers = { "Content-Type": "application/json" };
    if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf.slice("probe_csrf=".length));
    if (systemId != null) headers["X-Probe-System-Id"] = String(systemId);
    const res = await fetch(`/api${path}`, { method, headers, credentials: "include", body: body ? JSON.stringify(body) : undefined });
    const text = await res.text();
    return { status: res.status, body: text ? JSON.parse(text) : null };
  }, { method, path, body, systemId });
}

async function selectSystem(page, id) {
  await page.evaluate((id) => localStorage.setItem("probe_system_id", String(id)), id);
}

function overlaps(a, b) {
  return a.x < b.x + b.width - 0.5 && b.x < a.x + a.width - 0.5 && a.y < b.y + b.height - 0.5 && b.y < a.y + a.height - 0.5;
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROMIUM });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();

  // ── UX-09 / UX-18: deep link through login, unknown URL ─────────────────
  console.log("UX-09: 対象付き URL → ログイン → 同じ対象の画面");
  await page.goto(`${APP}/workspaces?open=1#decision`);
  await page.waitForURL(/\/login/);
  expect("login URL keeps the destination", new URL(page.url()).searchParams.get("next"), "/workspaces?open=1#decision");
  expectTrue("login page states where it returns", await page.getByTestId("login-return-to").isVisible());
  await page.getByLabel("Username").fill(USER);
  await page.getByLabel("Password").fill(PASS);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/workspaces/);
  const landed = new URL(page.url());
  expect("landed on the shared target", `${landed.pathname}${landed.search}${landed.hash}`, "/workspaces?open=1#decision");

  console.log("UX-09: 外部 URL は復帰先にしない");
  await page.goto(`${APP}/login?next=${encodeURIComponent("https://evil.example/")}`);
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  expect("external next is ignored", new URL(page.url()).origin + new URL(page.url()).pathname, `${APP}/`);

  // ── Fixture: two Systems, same-named tokens, one unbound token ──────────
  const longName = "とても長い名前のシステム-" + "x".repeat(40);
  const sysA = (await api(page, "POST", "/systems", { name: longName, environment: "production" })).body;
  const sysB = (await api(page, "POST", "/systems", { name: "beta", environment: "staging" })).body;
  const tokA = (await api(page, "POST", "/tokens/me", { name: "ci", system_id: sysA.id, expires_in_days: 30 })).body;
  const tokB = (await api(page, "POST", "/tokens/me", { name: "ci", system_id: sysB.id, expires_in_days: 30 })).body;
  expectTrue("fixture systems/tokens created", !!(sysA?.id && sysB?.id && tokA?.id && tokB?.id));
  await selectSystem(page, sysA.id);

  console.log("UX-18: 未知の URL");
  await page.goto(`${APP}/no-such-page?from=old-link`);
  await page.getByTestId("not-found").waitFor();
  expectTrue("heading ページが見つかりません", await page.getByRole("heading", { name: "ページが見つかりません" }).isVisible());
  expectTrue("header (AppLayout) still present", await page.getByRole("banner").isVisible());
  await page.getByTestId("not-found-overview").click();
  await page.waitForURL(`${APP}/`);
  expectTrue("Overview link recovers", true);

  // ── UX-01: token System scope ───────────────────────────────────────────
  console.log("UX-01: token の対象 System");
  await page.goto(`${APP}/connect-sdk`);
  await page.getByTestId(`sdk-token-row-${tokA.id}`).waitFor();
  expect("B's same-named token hidden by default", await page.getByTestId(`sdk-token-row-${tokB.id}`).count(), 0);
  expectTrue("System column names A", (await page.getByTestId(`sdk-token-system-${tokA.id}`).innerText()).includes(longName.slice(0, 10)));
  await page.getByTestId("sdk-token-scope-all").click();
  await page.getByTestId(`sdk-token-row-${tokB.id}`).waitFor();
  await page.getByRole("button", { name: `beta(#${sysB.id}) の token「ci」を失効させる` }).click();
  const revokeDialog = page.getByRole("dialog", { name: "Token を失効させますか?" });
  await revokeDialog.waitFor();
  expect("revoke confirmation names the target System", await revokeDialog.getByTestId("revoke-confirm-system").innerText(), `beta(#${sysB.id})`);
  expectTrue("initial focus on キャンセル", await revokeDialog.getByRole("button", { name: "キャンセル" }).evaluate((el) => el === document.activeElement));
  await page.screenshot({ path: `${OUT}/ux01-revoke-dialog.png` });
  await page.keyboard.press("Escape");
  expect("Escape closes the revoke dialog without revoking", await page.getByRole("dialog").count(), 0);
  const tokensAfter = (await api(page, "GET", "/tokens/me")).body;
  expect("nothing was revoked", tokensAfter.filter((t) => t.revoked).length, 0);

  // ── UX-08: clipboard ────────────────────────────────────────────────────
  console.log("UX-08: コピー失敗を成功と言わない");
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new DOMException("denied", "NotAllowedError")) },
    });
  });
  await page.getByPlaceholder("my-service").fill("svc-e2e");
  await page.getByRole("button", { name: "Token を発行" }).click();
  await page.getByTestId("issued-token-panel").waitFor();
  await page.getByTestId("issued-token-copy").click();
  await page.getByTestId("issued-token-select").waitFor();
  expectTrue("failure status shown", (await page.getByTestId("issued-token-copy-status").innerText()).includes("コピーできませんでした"));
  expectTrue("token kept on screen", (await page.getByTestId("issued-token-value").innerText()).length > 10);
  const selected = await page.evaluate(() => window.getSelection()?.toString() ?? "");
  expectTrue("token text is selected for manual copy", selected.length > 10);
  const successToast = await page.getByText("コピーしました", { exact: true }).count();
  expect("no success toast on failure", successToast, 0);

  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: APP });
  await page.reload();
  await page.getByPlaceholder("my-service").fill("svc-e2e-2");
  await page.getByRole("button", { name: "Token を発行" }).click();
  await page.getByTestId("issued-token-copy").click();
  await page.getByText("クリップボードにコピーしました。").waitFor();
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect("clipboard holds the issued token", clip, await page.getByTestId("issued-token-value").innerText());

  // ── UX-02 / UX-15: Dialog keyboard + labels ─────────────────────────────
  console.log("UX-02: 共通 Dialog");
  await page.goto(`${APP}/settings`);
  const createButton = page.getByTestId("header-create-system-button");
  await createButton.focus();
  await page.keyboard.press("Enter");
  const createDialog = page.getByRole("dialog", { name: "Create System" });
  await createDialog.waitFor();
  expect("exactly one dialog", await page.getByRole("dialog").count(), 1);
  expect("aria-modal", await createDialog.getAttribute("aria-modal"), "true");
  expect("focus moved to the first field", await page.evaluate(() => document.activeElement?.id), "name");
  for (let i = 0; i < 8; i += 1) await page.keyboard.press("Tab");
  expectTrue("Tab stays inside the dialog", await createDialog.evaluate((el) => el.contains(document.activeElement)));
  await page.keyboard.press("Shift+Tab");
  expectTrue("Shift+Tab stays inside the dialog", await createDialog.evaluate((el) => el.contains(document.activeElement)));
  expect("body scroll locked", await page.evaluate(() => document.body.style.overflow), "hidden");
  await page.screenshot({ path: `${OUT}/ux02-create-dialog.png` });
  await page.keyboard.press("Escape");
  expect("Escape closes", await page.getByRole("dialog").count(), 0);
  expectTrue("focus returned to the opener", await createButton.evaluate((el) => el === document.activeElement));
  expect("scroll lock released", await page.evaluate(() => document.body.style.overflow), "");

  console.log("UX-15: Settings のラベル");
  await page.getByLabel("System Name").click();
  expect("label click focuses the input", await page.evaluate(() => document.activeElement?.tagName), "INPUT");
  const envInput = page.getByRole("textbox", { name: "Environment" });
  expect("accessible name from the visible label", await envInput.inputValue(), "production");

  // ── UX-14: header at narrow widths ──────────────────────────────────────
  // Overlap is measured between the visible interactive controls themselves:
  // "fits in the viewport" alone would miss the audited collision.
  async function checkHeader(label, width) {
    await page.waitForTimeout(400);
    const { boxes, headerWidth, overflow } = await page.evaluate(() => {
      const header = document.querySelector("header");
      const els = Array.from(header.querySelectorAll("a, button, select, [data-testid='header-no-systems-hint'], [data-testid='user-phase-indicator'], [data-testid='user-phase-indicator-compact']"));
      const boxes = els
        .filter((el) => {
          const s = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
        })
        // A control nested in another measured element (e.g. a phase chip) is
        // not a collision with its own container.
        .filter((el, _i, all) => !all.some((other) => other !== el && other.contains(el)))
        .map((el) => {
          const r = el.getBoundingClientRect();
          return { id: el.getAttribute("data-testid") || el.getAttribute("aria-label") || el.tagName, x: r.x, y: r.y, width: r.width, height: r.height };
        });
      return { boxes, headerWidth: header.clientWidth, overflow: header.scrollWidth > header.clientWidth };
    });
    const collisions = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        if (overlaps(boxes[i], boxes[j])) collisions.push(`${boxes[i].id} × ${boxes[j].id}`);
      }
    }
    expect(`${label}: no overlapping header controls`, collisions, []);
    expectTrue(`${label}: header does not overflow`, !overflow);
    expectTrue(`${label}: every control inside the viewport`, boxes.every((b) => b.x >= 0 && b.x + b.width <= width + 0.5));
    expectTrue(`${label}: System select reachable`, boxes.some((b) => b.id === "header-system-select"));
    const select = boxes.find((b) => b.id === "header-system-select");
    expectTrue(`${label}: System select keeps a usable width (${Math.round(select?.width ?? 0)}px)`, (select?.width ?? 0) >= 96);
    if (headerWidth < 768) expectTrue(`${label}: overflow menu reachable`, boxes.some((b) => b.id === "header-overflow-toggle"));
    if (width < 768) expectTrue(`${label}: nav menu button reachable`, boxes.some((b) => b.id === "mobile-nav-toggle"));
    return headerWidth;
  }

  for (const width of [320, 390, 768, 1024, 1280]) {
    console.log(`UX-14: ヘッダー ${width}px (長い System 名・ユーザー名)`);
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${APP}/journey-blueprint`);
    await page.getByRole("banner").waitFor();
    const headerWidth = await checkHeader(`${width}px`, width);
    await page.screenshot({ path: `${OUT}/ux14-header-${width}.png` });
    if (headerWidth < 768) {
      await page.getByTestId("header-overflow-toggle").click();
      const menu = page.getByTestId("header-overflow-menu");
      await menu.waitFor();
      const box = await menu.boundingBox();
      expectTrue(`${width}px: overflow menu inside viewport`, box.x >= 0 && box.x + box.width <= width + 0.5);
      await page.keyboard.press("Escape");
    }
  }
  console.log("UX-14: 1280px でアシスタントを並べて開いた状態のヘッダー");
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`${APP}/journey-blueprint`);
  await page.getByTestId("assistant-button").click();
  await page.getByTestId("assistant-panel").waitFor();
  await checkHeader("1280px + assistant", 1280);
  await page.screenshot({ path: `${OUT}/ux14-header-1280-assistant.png` });
  await page.getByTestId("assistant-close").click();
  await page.setViewportSize({ width: 1280, height: 800 });

  // ── UX-10: Workspace URL sync ───────────────────────────────────────────
  console.log("UX-10: Workspace の選択と URL");
  const w1 = (await api(page, "POST", "/workspaces", { title: "E2E 判断 1" }, sysA.id)).body;
  const w2 = (await api(page, "POST", "/workspaces", { title: "E2E 判断 2" }, sysA.id)).body;
  await page.goto(`${APP}/workspaces`);
  await page.getByRole("button", { name: /E2E 判断 1/ }).click();
  await page.waitForURL(new RegExp(`open=${w1.id}`));
  await page.getByRole("button", { name: /E2E 判断 2/ }).click();
  await page.waitForURL(new RegExp(`open=${w2.id}`));
  await page.reload();
  await page.getByRole("button", { name: /E2E 判断 2/ }).waitFor();
  expect("reload keeps the selection", await page.getByRole("button", { name: /E2E 判断 2/ }).getAttribute("aria-current"), "true");
  await page.goBack();
  await page.waitForURL(new RegExp(`open=${w1.id}`));
  expect("back selects the previous one", await page.getByRole("button", { name: /E2E 判断 1/ }).getAttribute("aria-current"), "true");
  await page.goForward();
  await page.waitForURL(new RegExp(`open=${w2.id}`));
  expect("forward selects again", await page.getByRole("button", { name: /E2E 判断 2/ }).getAttribute("aria-current"), "true");
  await page.goto(`${APP}/workspaces?open=999999`);
  await page.getByTestId("workspace-back-to-list").click();
  await page.waitForURL((url) => !url.search.includes("open="));
  expectTrue("invalid id returns to the list", true);

  // ── UX-03: drafts survive tab switches and backdrop clicks ──────────────
  console.log("UX-03: Repository のタブ往復");
  await page.goto(`${APP}/repository`);
  const include = page.getByLabel(/Include Patterns/);
  await include.fill("*.py\nsrc/**\n*.ts");
  await page.getByRole("tab", { name: "Snapshots" }).click();
  await page.getByRole("tab", { name: "Configuration" }).click();
  expect("edited patterns survive the tab round trip", await page.getByLabel(/Include Patterns/).inputValue(), "*.py\nsrc/**\n*.ts");

  console.log("UX-03: 未保存入力の画面離脱で確認が出る");
  let dialogSeen = null;
  page.once("dialog", async (d) => { dialogSeen = d.message(); await d.dismiss(); });
  await page.getByRole("link", { name: /Setup Guide/ }).first().click();
  await page.waitForTimeout(300);
  expectTrue("discard confirmation before leaving", !!dialogSeen);
  expectTrue("still on Repository after dismiss", page.url().includes("/repository"));

  console.log("R2: ブラウザー履歴の戻るも破棄確認を通る");
  let backDialogs = 0;
  page.on("dialog", async (d) => { backDialogs += 1; await d.dismiss(); });
  // A cancelled back never completes a navigation, so page.goBack() would
  // wait for one; drive the browser history directly instead.
  await page.evaluate(() => history.back());
  await page.waitForTimeout(800);
  expect("one confirmation on history back", backDialogs, 1);
  expectTrue("URL stays on Repository after cancel", page.url().includes("/repository"));
  expect("input kept after cancelled back", await page.getByLabel(/Include Patterns/).inputValue(), "*.py\nsrc/**\n*.ts");
  page.removeAllListeners("dialog");
  page.once("dialog", (d) => d.accept());
  await page.evaluate(() => history.back());
  await page.waitForURL((url) => !url.pathname.startsWith("/repository"));
  expectTrue("accepted back leaves the page", true);

  console.log("UX-03 / UX-04: Experiment の下書きと行別エラー");
  await page.goto(`${APP}/experiments`);
  await page.getByTestId("experiment-create-button").click();
  let dialog = page.getByRole("dialog", { name: "Experimentを作成" });
  await dialog.waitFor();
  const longPatch = "diff --git a/x.py b/x.py\n" + "+line\n".repeat(80);
  await dialog.getByLabel("候補 1 のpatch").fill(longPatch);
  await page.mouse.click(5, 5); // backdrop
  expect("backdrop closed the dialog", await page.getByRole("dialog").count(), 0);
  expectTrue("draft notice shown", await page.getByTestId("experiment-draft-notice").isVisible());
  await page.getByTestId("experiment-create-button").click();
  dialog = page.getByRole("dialog", { name: "Experimentを作成" });
  expect("draft restored", await dialog.getByLabel("候補 1 のpatch").inputValue(), longPatch);

  // ── UX-05 / UX-06 / UX-12: failure presentation ─────────────────────────
  console.log("UX-05: Probe plan 一覧の取得失敗");
  await page.route("**/api/repository/probe-plans", (route) => route.fulfill({ status: 500, body: JSON.stringify({ detail: "injected" }) }));
  await page.goto(`${APP}/probe-planner`);
  await page.getByTestId("probe-plans-error").waitFor();
  expect("no 0-件 message on failure", await page.getByText(/No probe plans yet/).count(), 0);
  await page.unroute("**/api/repository/probe-plans");
  await page.getByTestId("probe-plans-error").getByRole("button", { name: "再試行" }).click();
  await page.getByText(/No probe plans yet/).waitFor();
  expectTrue("retry recovers to the real 0 件", true);

  console.log("UX-12: Journey が 0 件なら作成へ誘導");
  await page.goto(`${APP}/journey-blueprint`);
  await page.getByTestId("blueprint-no-journeys").waitFor();
  expect("no 選択してください as primary guidance", await page.getByTestId("blueprint-no-journey").count(), 0);
  await page.getByTestId("blueprint-create-journey").click();
  await page.waitForURL(/ux-design-studio\?tab=journeys/);
  expectTrue("CTA reaches UX Design Studio journeys", true);

  console.log("UX-06: System 一覧の取得失敗は『未作成』ではない");
  await page.route("**/api/systems", (route) => route.fulfill({ status: 500, body: JSON.stringify({ detail: "injected" }) }));
  await page.goto(`${APP}/`);
  await page.getByTestId("systems-load-error").waitFor();
  expect("header does not claim zero Systems", await page.getByTestId("header-no-systems-hint").count(), 0);
  await page.unroute("**/api/systems");
  await page.getByTestId("systems-load-error").getByRole("button", { name: "System 一覧を再取得" }).click();
  await page.getByTestId("systems-load-error").waitFor({ state: "detached" });
  expectTrue("retry clears the banner", true);

  console.log("UX-06: サーバー到達不能はログイン画面へ送らない");
  await page.route("**/api/auth/me", (route) => route.abort("connectionrefused"));
  await page.goto(`${APP}/components`);
  await page.getByTestId("auth-bootstrap-error").waitFor();
  expectTrue("not redirected to /login", !page.url().includes("/login"));
  await page.screenshot({ path: `${OUT}/ux06-unreachable.png` });
  await page.unroute("**/api/auth/me");
  await page.getByTestId("auth-bootstrap-retry").click();
  await page.getByRole("banner").waitFor();
  expectTrue("retry resumes the app", true);

  console.log("UX-06: 利用中の 401 はその場で再認証し、入力を保つ");
  await page.goto(`${APP}/settings`);
  await page.getByLabel("Description").fill("keep-this-draft");
  // Every non-auth API call now answers 401, as an expired session does. The
  // app's own client has to make the request, so it is triggered by a real
  // user action (Save) rather than a raw fetch.
  await page.route("**/api/**", (route) => {
    if (route.request().url().includes("/api/auth/")) return route.continue();
    return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "expired" }) });
  });
  await page.getByRole("button", { name: "Save Settings" }).click();
  const reauth = page.getByRole("dialog", { name: "ログインの有効期限が切れました" });
  await reauth.waitFor({ timeout: 10000 });
  expect("one re-auth dialog despite several 401s", await page.getByRole("dialog").count(), 1);
  expectTrue("still on the same page (no redirect)", page.url().includes("/settings"));
  await page.keyboard.press("Escape");
  expect("Escape does not dismiss the re-auth choice", await page.getByRole("dialog").count(), 1);
  await page.unroute("**/api/**");

  console.log("R1: 再ログイン POST 成功後に /auth/me が失敗しても入力を保つ");
  await page.route("**/api/auth/me", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "injected" }) }));
  await reauth.getByLabel("パスワード").fill(PASS);
  await reauth.getByTestId("reauth-submit").click();
  await reauth.getByText(/ログイン状態を確認できませんでした/).waitFor();
  expect("no connection-error screen", await page.getByTestId("auth-bootstrap-error").count(), 0);
  expect("draft kept while /auth/me fails", await page.getByLabel("Description").inputValue(), "keep-this-draft");
  await page.unroute("**/api/auth/me");

  await reauth.getByTestId("reauth-submit").click();
  await reauth.waitFor({ state: "detached" });
  expect("draft survives re-authentication", await page.getByLabel("Description").inputValue(), "keep-this-draft");

  // ── UX-11 / UX-19: Setup Guide ──────────────────────────────────────────
  console.log("UX-11 / UX-19: Setup Guide");
  await page.goto(`${APP}/setup-guide`);
  await page.getByTestId("setup-next-step").waitFor();
  const cta = page.getByTestId("setup-next-step-cta");
  expectTrue("one next-step CTA", (await cta.count()) === 1);
  await page.getByRole("tab", { name: "ホストで直接実行" }).click();
  await page.getByLabel("対象アプリの環境へprobe-agent SDKを導入した").check();
  await page.goto(`${APP}/connect-sdk`);
  await page.goBack();
  await page.getByTestId("runtime-checklist-host").waitFor();
  expectTrue("progress restored after visiting Connect SDK", await page.getByLabel("対象アプリの環境へprobe-agent SDKを導入した").isChecked());
  expectTrue("manual vs server status shown separately", await page.getByTestId("runtime-checklist-server-status").isVisible());

  // ── UX-13: navigation ───────────────────────────────────────────────────
  console.log("UX-13: ナビゲーション");
  await page.goto(`${APP}/`);
  const rail = page.getByTestId("sidebar-rail");
  await rail.waitFor();
  await rail.getByTestId("sidebar-purpose-entries").waitFor();
  await page.screenshot({ path: `${OUT}/ux13-nav.png` });
  expectTrue("purpose entries visible", await rail.getByTestId("sidebar-purpose-entries").isVisible());
  await rail.getByTestId("sidebar-search").fill("blueprint");
  await rail.getByRole("link", { name: /Journey Service Blueprint/ }).click();
  await page.waitForURL(/journey-blueprint/);
  await rail.getByTestId("sidebar-current-badge").first().waitFor();
  expectTrue("current page badge", await rail.getByTestId("sidebar-current-badge").first().isVisible());
  const futureOpacity = await page.evaluate(() => {
    const groups = Array.from(document.querySelectorAll("[data-phase-state='future']"));
    return groups.map((g) => getComputedStyle(g).opacity);
  });
  expectTrue("no dimmed groups", futureOpacity.every((o) => o === "1"));

  // ── UX-17 / UX-16 / UX-20: assistant ────────────────────────────────────
  console.log("UX-17: 1280px では本文と並ぶ");
  await page.goto(`${APP}/repository`);
  await page.getByTestId("assistant-button").click();
  const panel = page.getByTestId("assistant-panel");
  await panel.waitFor();
  expect("side-by-side layout", await panel.getAttribute("data-layout"), "side");
  expect("no backdrop", await page.getByTestId("assistant-backdrop").count(), 0);
  const panelBox = await panel.boundingBox();
  const mainBox = await page.locator("main").boundingBox();
  expectTrue("panel does not cover main", mainBox.x + mainBox.width <= panelBox.x + 1);
  await page.getByTestId("assistant-question-input").fill("複数条件の相談");
  await page.getByLabel(/Exclude Patterns/).fill("edited while panel open");
  expect("page input editable with panel open", await page.getByLabel(/Exclude Patterns/).inputValue(), "edited while panel open");
  expect("assistant draft kept", await page.getByTestId("assistant-question-input").inputValue(), "複数条件の相談");
  await page.screenshot({ path: `${OUT}/ux17-side-by-side.png` });

  console.log("UX-20: Shift+Enter で改行、IME 変換中の Enter では送信しない");
  const q = page.getByTestId("assistant-question-input");
  await q.fill("1行目");
  await q.press("Shift+Enter");
  await q.type("2行目");
  expect("Shift+Enter inserts a newline", await q.inputValue(), "1行目\n2行目");
  let asked = 0;
  page.on("request", (r) => { if (r.url().endsWith("/api/assistant/ask")) asked += 1; });
  const cdp = await context.newCDPSession(page);
  await q.focus();
  await cdp.send("Input.imeSetComposition", { text: "へんかん", selectionStart: 4, selectionEnd: 4 });
  await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 229, nativeVirtualKeyCode: 229 });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 229 });
  await cdp.send("Input.insertText", { text: "変換" });
  await page.waitForTimeout(400);
  expect("no request sent during IME composition Enter", asked, 0);
  const height1 = (await q.boundingBox()).height;
  await q.fill("a\nb\nc\nd\ne");
  const height2 = (await q.boundingBox()).height;
  expectTrue(`textarea grows with content (${height1} → ${height2})`, height2 > height1);

  console.log("UX-16: ライブ領域 (Chromium のアクセシビリティツリー)");
  expect("status region role", await page.getByTestId("assistant-live-status").getAttribute("role"), "status");
  expect("alert region role", await page.getByTestId("assistant-live-alert").getAttribute("role"), "alert");

  console.log("UX-17: 390px ではモーダル");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  expect("overlay layout on narrow screens", await panel.getAttribute("data-layout"), "overlay");
  expect("dialog role", await panel.getAttribute("role"), "dialog");
  expect("assistant draft kept across the layout switch", await page.getByTestId("assistant-question-input").inputValue(), "a\nb\nc\nd\ne");
  await page.screenshot({ path: `${OUT}/ux17-overlay-390.png` });

  await browser.close();
  if (failures.length) {
    console.log(`\n${failures.length} failure(s):`);
    for (const f of failures) console.log(" - " + f);
    process.exit(1);
  }
  console.log("\nall browser checks passed");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
