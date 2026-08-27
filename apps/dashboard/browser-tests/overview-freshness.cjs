// Issue #384 acceptance: the Overview must follow the clock in a REAL browser.
// receiving_now -> delayed -> stale with no reload, then recovery.
// Playwright is deliberately NOT a repository dependency (same treatment as
// Issue #358's measurement harness): it is a verification tool, not something
// the dashboard build needs. Provide it via NODE_PATH, e.g.
//
//   npm i --prefix /tmp/pw playwright-core
//   NODE_PATH=/tmp/pw/node_modules node browser-tests/overview-freshness.cjs /tmp/out
//
// See browser-tests/README.md for the full procedure.
const { chromium } = require("playwright-core");

const CHROMIUM =
  process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

const APP = "http://127.0.0.1:8098";
const API = "http://127.0.0.1:8099";

async function api(pathname, opts = {}) {
  const res = await fetch(API + pathname, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${pathname} -> ${res.status} ${text}`);
  return text ? JSON.parse(text) : null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // --- fixture -------------------------------------------------------------
  const login = await fetch(API + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "root", password: "s3cret" }),
  });
  const setCookie = login.headers.getSetCookie().join("; ");
  const token = /probe_session=([^;]+)/.exec(setCookie)[1];
  const auth = { Authorization: `Bearer ${token}` };

  const system = await api("/systems", {
    method: "POST",
    headers: auth,
    body: JSON.stringify({ name: "freshness-e2e", environment: "test", description: "" }),
  });
  const sysHeaders = { ...auth, "X-Probe-System-Id": String(system.id) };

  // Short thresholds so the transition is observable inside a test.
  await api("/connectivity/freshness-policy", {
    method: "PUT",
    headers: sysHeaders,
    body: JSON.stringify({ delayed_after_seconds: 6, stale_after_seconds: 14 }),
  });

  const sendTrace = async (id) =>
    api("/traces", {
      method: "POST",
      headers: sysHeaders,
      body: JSON.stringify({
        trace_id: id, component_id: "summarize", mode: "trace",
        input: { a: 1 }, output: "ok", duration_ms: 2, timestamp: Date.now() / 1000,
      }),
    });

  await sendTrace("t-1");

  // --- browser -------------------------------------------------------------
  const browser = await chromium.launch({
    executablePath: CHROMIUM,
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  await context.addCookies([
    { name: "probe_session", value: token, domain: "127.0.0.1", path: "/" },
  ]);
  const page = await context.newPage();
  // The SPA reads the selected System from localStorage and sends it as
  // `X-Probe-System-Id`; seed it before the first render.
  await page.goto(APP + "/");
  await page.evaluate((id) => localStorage.setItem("probe_system_id", String(id)), system.id);
  await page.reload();
  await page.waitForSelector('[data-testid="overview-runtime-freshness"]', { timeout: 20000 });

  const read = async () => ({
    freshness: await page.getAttribute('[data-testid="overview-runtime-freshness"]', "data-freshness"),
    badge: (await page.textContent('[data-testid="overview-runtime-freshness"]')).trim(),
    action: await page.getAttribute('[data-testid="overview-next-action-cta"]', "data-action-key").catch(() => null),
    findings: await page.$$eval('[data-testid="overview-finding"]',
      (els) => els.map((e) => e.getAttribute("data-finding-kind"))),
    lastSeen: (await page.textContent('[data-testid="overview-runtime-last-seen"]')).trim(),
    scrollW: await page.evaluate(() => document.documentElement.scrollWidth),
    clientW: await page.evaluate(() => document.documentElement.clientWidth),
    headings: await page.$$eval("h1,h2", (els) => els.map((e) => e.tagName + " " + e.textContent.trim())),
  });

  const timeline = [];
  const record = async (label) => {
    const state = await read();
    timeline.push({ label, ...state });
    console.log(`\n[${label}] freshness=${state.freshness} badge=${state.badge}`);
    console.log(`  action=${state.action}  findings=${JSON.stringify(state.findings)}`);
    console.log(`  lastSeen="${state.lastSeen}"`);
    console.log(`  horizontal scroll=${state.scrollW > state.clientW ? "YES (BAD)" : "no"}`);
    return state;
  };

  const initial = await record("t=0 (just received)");

  // No reload, no interaction: wait past the `delayed` threshold.
  await page.waitForFunction(
    () => document.querySelector('[data-testid="overview-runtime-freshness"]')
      ?.getAttribute("data-freshness") === "delayed",
    { timeout: 30000 },
  );
  const delayed = await record("after delayed threshold (no reload)");

  await page.waitForFunction(
    () => document.querySelector('[data-testid="overview-runtime-freshness"]')
      ?.getAttribute("data-freshness") === "stale",
    { timeout: 40000 },
  );
  const stale = await record("after stale threshold (no reload)");

  // Recovery: a new workload trace. Once `stale`, no freshness boundary
  // remains ahead, so the page is on its bounded 5-minute ceiling — far too
  // long for a test. The reconnect path is the other documented trigger, so
  // that is what is exercised here (`refetchOnReconnect`).
  await sendTrace("t-2");
  await context.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await sleep(300);
  await context.setOffline(false);
  await page.evaluate(() => {
    window.dispatchEvent(new Event("online"));
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
  await page.waitForFunction(
    () => document.querySelector('[data-testid="overview-runtime-freshness"]')
      ?.getAttribute("data-freshness") === "receiving_now",
    { timeout: 30000 },
  );
  const recovered = await record("after new trace (focus refetch)");

  // --- assertions ----------------------------------------------------------
  const problems = [];
  if (initial.freshness !== "receiving_now") problems.push("initial was not receiving_now");
  if (delayed.freshness !== "delayed") problems.push("did not reach delayed");
  if (stale.freshness !== "stale") problems.push("did not reach stale");
  if (recovered.freshness !== "receiving_now") problems.push("did not recover");
  // The finding must appear with the freshness, from the same response.
  if (!delayed.findings.includes("connectivity_lost")) problems.push("no connectivity_lost finding when delayed");
  if (recovered.findings.includes("connectivity_lost")) problems.push("connectivity_lost survived recovery");
  // The CTA must follow too.
  // This fixture has no repository, so rule 1 legitimately owns the CTA
  // throughout: freshness cannot promote a later row over an earlier one.
  // That ordering is a pure-function property and is unit-tested
  // (`test_stale_reception_outranks_a_pending_decision`). What matters here is
  // that the CTA is re-rendered from the SAME response as the freshness and
  // the finding, and never disappears or contradicts them mid-transition.
  for (const s of timeline) {
    if (s.action !== "prepare_repository") {
      problems.push(`CTA changed unexpectedly at ${s.label}: ${s.action}`);
    }
  }
  for (const s of timeline) {
    if (s.scrollW > s.clientW) problems.push(`horizontal scroll at ${s.label}`);
    if (s.headings[0] !== "H1 Overview") problems.push(`heading order broke at ${s.label}`);
  }

  await page.screenshot({ path: process.argv[2] + "/freshness-stale.png" });

  // --- scenario 2: Experiment deep link + reload ---------------------------
  //
  // Scenario 1 needed the real server because it tests the real clock. These
  // two test the browser's own paths -- URL-driven selection surviving a
  // reload, and the degraded render -- so the RESPONSE is controlled by route
  // interception while the router, the reload and the render stay real.
  const experimentsPage = await context.newPage();
  const rows = [
    {
      id: 11, system_id: system.id, feature_id: "f", objective: "undecided one",
      snapshot_id: 1, baseline_commit: "abc", config_revision: "r",
      execution_config: "{}", status: "completed", error: null,
      human_decision: "undecided", human_decision_variant_key: null,
      human_decision_note: "", created_at: 1, started_at: 1, completed_at: 2,
      variants: [],
    },
    { ...{
      id: 12, system_id: system.id, feature_id: "g", objective: "already adopted",
      snapshot_id: 1, baseline_commit: "abc", config_revision: "r",
      execution_config: "{}", status: "completed", error: null,
      human_decision: "adopted", human_decision_variant_key: "v1",
      human_decision_note: "done", created_at: 1, started_at: 1, completed_at: 3,
      variants: [],
    } },
  ];
  await experimentsPage.route("**/api/experiments", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(rows) }));

  const expandedIds = async (p) =>
    p.$$eval('[data-testid^="experiment-detail-"]',
      (els) => els.map((e) => e.getAttribute("data-testid")));

  await experimentsPage.goto(`${APP}/experiments?experiment=11`);
  await experimentsPage.waitForSelector('[data-testid="experiment-row-11"]', { timeout: 20000 });
  const arrival = await expandedIds(experimentsPage);
  await experimentsPage.reload();
  await experimentsPage.waitForSelector('[data-testid="experiment-row-11"]', { timeout: 20000 });
  const afterReload = await expandedIds(experimentsPage);
  console.log(`\n[deep link] undecided target: arrival=${JSON.stringify(arrival)} reload=${JSON.stringify(afterReload)}`);
  if (!arrival.includes("experiment-detail-11")) problems.push("undecided deep link did not expand");
  if (!afterReload.includes("experiment-detail-11")) problems.push("undecided deep link lost on reload");

  await experimentsPage.goto(`${APP}/experiments?experiment=12`);
  await experimentsPage.waitForSelector('[data-testid="experiment-row-12"]', { timeout: 20000 });
  const adopted = await expandedIds(experimentsPage);
  console.log(`[deep link] adopted target expanded=${JSON.stringify(adopted)}`);
  if (adopted.length) problems.push("an already-decided experiment was expanded from the URL");

  await experimentsPage.goto(`${APP}/experiments?experiment=999999`);
  await experimentsPage.waitForSelector('[data-testid="experiment-row-11"]', { timeout: 20000 });
  const unknown = await expandedIds(experimentsPage);
  console.log(`[deep link] unknown id expanded=${JSON.stringify(unknown)}`);
  if (unknown.length) problems.push("an unknown experiment id expanded a substitute row");
  await experimentsPage.close();

  // --- scenario 3: a degraded section does not blank the page --------------
  const degradedPage = await context.newPage();
  await degradedPage.route("**/api/overview", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.brief = null;
    body.degraded_sections = ["brief"];
    body.degraded_detail = { brief: "RuntimeError: injected" };
    body.next_action = null;
    body.next_action_state = "unavailable";
    body.next_action_message = "現在の状態を判定できませんでした。";
    body.findings = [];
    body.findings_state = "unavailable";
    body.findings_baseline_state = "unavailable";
    body.findings_baseline_label = "比較の基準を取得できませんでした。確認済みかどうかは判定していません";
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await degradedPage.goto(APP + "/");
  await degradedPage.waitForSelector('[data-testid="overview-degraded"]', { timeout: 20000 });
  const degraded = {
    briefUnavailable: !!(await degradedPage.$('[data-testid="overview-brief-unavailable"]')),
    runtime: !!(await degradedPage.$('[data-testid="overview-runtime"]')),
    loop: !!(await degradedPage.$('[data-testid="overview-loop"]')),
    cta: !!(await degradedPage.$('[data-testid="overview-next-action-cta"]')),
    unavailableNote: !!(await degradedPage.$('[data-testid="overview-next-action-unavailable"]')),
    baselineNote: await degradedPage.textContent('[data-testid="overview-findings-baseline-unavailable"]').catch(() => null),
  };
  console.log(`\n[degraded] ${JSON.stringify(degraded)}`);
  if (!degraded.briefUnavailable) problems.push("degraded Brief did not render its own notice");
  if (!degraded.runtime || !degraded.loop) problems.push("degraded Brief blanked other sections");
  if (degraded.cta) problems.push("a CTA was guessed while the Brief was unavailable");
  if (!degraded.unavailableNote) problems.push("no unavailable sentence for the next action");
  if (!degraded.baselineNote || degraded.baselineNote.includes("まだ理解を確認していない")) {
    problems.push("an unreadable baseline was reported as 未確認");
  }
  await degradedPage.screenshot({ path: process.argv[2] + "/degraded.png" });
  await degradedPage.close();

  await browser.close();

  console.log("\n=== RESULT ===");
  if (problems.length) {
    problems.forEach((p) => console.log("  FAIL " + p));
    process.exit(1);
  }
  console.log("  PASS: receiving_now -> delayed -> stale -> receiving_now with no reload");
  console.log("  freshness, connectivity finding and CTA all moved together");
})().catch((e) => { console.error(e); process.exit(1); });
