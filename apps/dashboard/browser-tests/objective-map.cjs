// Issue #427 / #432 acceptance in a REAL browser (review §4.1).
//
// `vitest` runs in jsdom: no layout, no real navigation, no history stack, and
// no focus model worth trusting. Six of this screen's guarantees are therefore
// unverifiable there, and every one of them is a way the screen fails while
// its unit tests stay green:
//
//   * a deep link that lands on a COLLAPSED ancestor shows nothing;
//   * a reload that loses the selection;
//   * back/forward leaving the selection and the Gap filters disagreeing;
//   * one pending lane blocking the other;
//   * an unreadable Gap count rendering as 0 件;
//   * tabs and the tree being unreachable from the keyboard.
//
// Responses are supplied by route interception. What is under test here is the
// BROWSER's behaviour -- router, history, reload, focus, layout -- not the
// server's projection, which the pytest suite already covers.
//
//   npm i --prefix /tmp/pw playwright-core
//   NODE_PATH=/tmp/pw/node_modules node browser-tests/objective-map.cjs /tmp/out
//
// See browser-tests/README.md for the full procedure.
const { chromium } = require("playwright-core");

const CHROMIUM =
  process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

const APP = "http://127.0.0.1:8098";
const OUT = process.argv[2] ?? "/tmp/objective-map-e2e";

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

// --- fixture ----------------------------------------------------------------
// A two-level Objective tree: the deep-link target `o-child` sits UNDER
// `o-root`, so a link to it is only visible if the ancestor is force-opened.
function gapSummary(over = {}) {
  return {
    open_count: 0, acknowledged_count: 0, deferred_count: 0,
    resolved_count: 0, rejected_count: 0, obsolete_count: 0,
    recheck_required_count: 0, reopen_candidate_count: 0, close_candidate_count: 0,
    ...over,
  };
}

const objectiveMap = {
  system_id: 1,
  generated_at: 1000,
  nodes: [
    {
      id: 1, objective_key: "o-root", title: "決済体験を良くする", objective_state: "active",
      recheck_state: "current", parent_objective_id: null, parent_objective_key: null,
      child_objective_ids: [2], milestones: [],
    },
    {
      id: 2, objective_key: "o-child", title: "初回決済の離脱を減らす", objective_state: "active",
      recheck_state: "current", parent_objective_id: 1, parent_objective_key: "o-root",
      child_objective_ids: [],
      milestones: [
        {
          id: 1, milestone_key: "m-first", title: "初回決済が手戻りなく完了する",
          design_status: "confirmed", achievement: "unassessed", assessability: "assessable",
          recheck_state: "current", sequence_hint: 0, gap_summary: gapSummary({ open_count: 2 }),
        },
        // §3.2: this Milestone's Gap read FAILED. The server still returns a
        // well-formed summary -- an ALL-ZERO one -- and signals the failure
        // only through `degraded_sections`. That is exactly why the bug was
        // possible: the zeros are indistinguishable from a real "no Gaps"
        // unless the reader checks the degraded list, so the fixture uses the
        // shape the server actually sends rather than a null the client would
        // never receive.
        {
          id: 2, milestone_key: "m-unreadable", title: "再訪率が戻る",
          design_status: "confirmed", achievement: "unassessed", assessability: "assessable",
          recheck_state: "current", sequence_hint: 1, gap_summary: gapSummary(),
        },
      ],
    },
  ],
  root_objective_ids: [1],
  degraded_sections: ["gaps:m-unreadable"],
  degraded_detail: { "gaps:m-unreadable": "OperationalError: no such table" },
};

const gapWorkbench = {
  system_id: 1, generated_at: 1000,
  entries: [
    {
      id: 1, gap_key: "g-form", milestone_id: 1, milestone_key: "m-first",
      objective_id: 2, objective_key: "o-child", title: "入力項目が多く離脱する",
      lifecycle: "open", priority_band: "now", recheck_state: "current",
      read_flags: [], deep_links: [],
    },
  ],
  source_kind_breakdown: [{ source_kind: "manual", gap_count: 1 }],
  shared_sources: [],
  degraded_sections: [], degraded_detail: {},
};

const API = "http://127.0.0.1:8099";

(async () => {
  const login = await fetch(API + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "root", password: "s3cret" }),
  });
  if (!login.ok) throw new Error(`login -> ${login.status}`);
  const token = /probe_session=([^;]+)/.exec(login.headers.getSetCookie().join("; "))[1];
  const systemRes = await fetch(API + "/systems", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ name: `objective-map-e2e-${Date.now()}`, environment: "test", description: "" }),
  });
  if (!systemRes.ok) throw new Error(`create system -> ${systemRes.status}`);
  const system = await systemRes.json();

  const browser = await chromium.launch({ executablePath: CHROMIUM });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  await context.addCookies([
    { name: "probe_session", value: token, domain: "127.0.0.1", path: "/" },
  ]);
  const page = await context.newPage();

  // Auth and System selection run against the REAL server, exactly as the
  // freshness scenario does. Stubbing them means guessing shapes the shell
  // depends on, and a wrong guess renders the login screen rather than a
  // failure anyone can read. Only the two projections are intercepted.

  // Lane latency is per-scenario, so both routes read a mutable delay.
  let mapDelayMs = 0;
  let workbenchDelayMs = 0;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  await page.route("**/api/objective-map**", async (route) => {
    if (mapDelayMs) await sleep(mapDelayMs);
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(objectiveMap) });
  });
  await page.route("**/api/gap-workbench**", async (route) => {
    if (workbenchDelayMs) await sleep(workbenchDelayMs);
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(gapWorkbench) });
  });
  await page.route("**/api/product-gaps/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      id: 1, system_id: 1, gap_key: "g-form", milestone_id: 1, milestone_key: "m-first",
      objective_id: 2, objective_key: "o-child", title: "入力項目が多く離脱する",
      lifecycle: "open", priority_band: "now", recheck_state: "current", read_flags: [],
      created_by: "dev", created_at: 1000, updated_at: 1000,
      current_revision_id: 1, current_revision_number: 1, decision_digest: "gap-digest",
      effective_target_state: "10% 未満", effective_target_availability: "own",
      current_revision: null,
      source_refs: [], journey_links: [], evidence_refs: [], artifact_links: [], decisions: [],
      degraded_sections: [], degraded_detail: {},
    }) }));

  await page.goto(APP + "/");
  await page.evaluate((id) => localStorage.setItem("probe_system_id", String(id)), system.id);

  // --- 1. nested deep link + reload (§3.1) ----------------------------------
  console.log("\n[1] deep link to a NESTED Objective, then reload");
  await page.goto(APP + "/objective-map?objective=o-child");
  await page.waitForSelector('[data-testid="objective-node-o-child"]', { timeout: 20000 });
  expectTrue("the nested target is visible without any click",
    await page.isVisible('[data-testid="objective-node-o-child"]'));
  expectTrue("its ancestor is expanded",
    await page.isVisible('[data-testid="objective-children-o-root"]'));
  expectTrue("its detail card is shown",
    await page.isVisible('[data-testid="objective-detail-o-child"]'));

  await page.reload();
  await page.waitForSelector('[data-testid="objective-detail-o-child"]', { timeout: 20000 });
  expectTrue("the selection survives a real reload",
    await page.isVisible('[data-testid="objective-detail-o-child"]'));

  // --- 2. an unreadable Gap count is not 0 件 (§3.2) -------------------------
  console.log("\n[2] an unreadable Gap read is never rendered as a count");
  expectTrue("the unreadable Milestone says so",
    await page.isVisible('[data-testid="milestone-gap-summary-unavailable-m-unreadable"]'));
  const unreadableText = (await page.textContent(
    '[data-testid="milestone-gap-summary-unavailable-m-unreadable"]')).trim();
  expectTrue(`it does not read as a zero count ("${unreadableText}")`,
    !/(^|[^0-9])0\s*件/.test(unreadableText));
  const objectiveTotal = (await page.textContent('[data-testid="objective-gap-total-o-child"]')).trim();
  // The readable Milestone alone has 2 open Gaps. The unreadable one must not
  // be silently added in as 0 -- the total either excludes it and says so, or
  // reports itself unreadable.
  expectTrue(`the Objective total does not absorb the unreadable branch ("${objectiveTotal}")`,
    !/Gap\s*2\s*件$/.test(objectiveTotal) || objectiveTotal.includes("取得"));

  // --- 3. back / forward keeps selection and filters together (§3.4) --------
  //
  // A tree click deliberately uses `replace: true` -- selecting a node is not
  // a navigation and should not put an entry in the history stack. So this
  // exercises what §3.4 actually protects: when the URL changes UNDERNEATH
  // the page, does the selection re-sync, and does the Gap Workbench filter
  // follow it? The filter is the half that used to drift, because it was
  // seeded from the URL on first render only.
  console.log("\n[3] back/forward round trip (URL-driven, not click-driven)");
  await page.goto(APP + "/objective-map?objective=o-root");
  await page.waitForSelector('[data-testid="objective-detail-o-root"]', { timeout: 10000 });

  const filterObjective = async () => {
    await page.click('[data-testid="objective-map-tab-gaps"]');
    await page.waitForSelector('[data-testid="gap-workbench-filters"]', { timeout: 10000 });
    return page.$eval('[data-testid="gap-workbench-filters"] select', (el) => el.value);
  };

  await page.goBack();
  await page.waitForSelector('[data-testid="objective-detail-o-child"]', { timeout: 10000 });
  expectTrue("back restores the earlier selection",
    await page.isVisible('[data-testid="objective-detail-o-child"]'));
  expect("the Gap filter follows it back", await filterObjective(), "o-child");

  await page.click('[data-testid="objective-map-tab-objectives"]');
  await page.goForward();
  await page.waitForSelector('[data-testid="objective-detail-o-root"]', { timeout: 10000 });
  expectTrue("forward restores the later selection",
    await page.isVisible('[data-testid="objective-detail-o-root"]'));
  expect("the Gap filter follows it forward", await filterObjective(), "o-root");

  // --- 4. one lane pending does not block the other (§3.3) -----------------
  //
  // The Gap lane's panel mounts only when its tab is active, so the tab is
  // opened FIRST and the delay is long enough that the response cannot have
  // arrived by then. A shorter delay makes this assertion race the fetch and
  // pass for the wrong reason.
  console.log("\n[4] a slow Gap Workbench does not block the Objective lane");
  workbenchDelayMs = 20000;
  await page.goto(APP + "/objective-map?view=gaps&objective=o-child");
  await page.waitForSelector('[data-testid="gap-workbench-lane-loading"]', { timeout: 20000 });
  expectTrue("the pending lane names itself rather than showing a bare skeleton",
    await page.isVisible('[data-testid="gap-workbench-lane-loading"]'));

  await page.click('[data-testid="objective-map-tab-objectives"]');
  await page.waitForSelector('[data-testid="objective-node-o-child"]', { timeout: 20000 });
  expectTrue("the Objective lane is fully usable while the other is still pending",
    await page.isVisible('[data-testid="objective-node-o-child"]'));
  expectTrue("and its deep-linked target is revealed, not blocked",
    await page.isVisible('[data-testid="objective-detail-o-child"]'));
  workbenchDelayMs = 0;

  // --- 5. tabs are keyboard-operable and correctly labelled (§3.6) ----------
  console.log("\n[5] WAI-ARIA tabs");
  await page.goto(APP + "/objective-map?objective=o-child");
  await page.waitForSelector('[data-testid="objective-map-tabs"]', { timeout: 20000 });
  const roles = await page.evaluate(() => {
    const list = document.querySelector('[data-testid="objective-map-tabs"]');
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    return {
      tablist: list.getAttribute("role"),
      tabCount: tabs.length,
      selected: tabs.map((t) => t.getAttribute("aria-selected")),
      controls: tabs.every((t) => !!t.getAttribute("aria-controls")),
    };
  });
  expect("the container is a tablist", roles.tablist, "tablist");
  expect("both lanes are tabs", roles.tabCount, 2);
  expect("exactly one is selected", roles.selected.filter((v) => v === "true").length, 1);
  expectTrue("every tab points at its panel", roles.controls);

  await page.focus('[data-testid="objective-map-tab-objectives"]');
  await page.keyboard.press("ArrowRight");
  const focusedAfterArrow = await page.evaluate(() =>
    document.activeElement?.getAttribute("data-testid"));
  expect("ArrowRight moves focus to the next tab", focusedAfterArrow, "objective-map-tab-gaps");
  await page.keyboard.press("Enter");
  await page.waitForSelector('[data-testid="objective-map-panel-gaps"]', { timeout: 10000 });
  expectTrue("Enter activates the focused tab",
    await page.isVisible('[data-testid="objective-map-panel-gaps"]'));

  // --- 6. narrow viewport keeps the same meaning order ----------------------
  console.log("\n[6] 390px viewport");
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto(APP + "/objective-map?objective=o-child");
  await page.waitForSelector('[data-testid="objective-node-o-child"]', { timeout: 20000 });
  const narrow = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expectTrue(`no horizontal page scroll (${narrow.scrollW} vs ${narrow.clientW})`,
    narrow.scrollW <= narrow.clientW + 1);
  await page.screenshot({ path: `${OUT}/objective-map-390.png`, fullPage: true });

  await browser.close();

  console.log(`\n${failures.length === 0 ? "ALL PASSED" : `${failures.length} FAILED`}`);
  for (const f of failures) console.log("  - " + f);
  process.exit(failures.length === 0 ? 0 : 1);
})().catch((err) => { console.error(err); process.exit(1); });
