// Browser tests of the Agento Product Control Center (Task 038), against the built Web
// app (`npm run build` first) and the TEST-ONLY stub upstream (control-center-stub.mjs).
//
//   NODE_PATH="$(npm root -g)" node scripts/control-center.browser.mjs
//
// Playwright is NOT a dependency of this package: the script uses a Playwright install
// already present on the machine (resolved through NODE_PATH) and a local Chromium. It
// makes no network request beyond 127.0.0.1. No pixel-golden screenshots: set
// SCREENSHOT_DIR to keep review screenshots of each viewport.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { startStub } from "./control-center-stub.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const NEXT_BIN = join(WEB_ROOT, "node_modules", "next", "dist", "bin", "next");
const GOOD = "stub-key-good";
const SCREENSHOTS = process.env.SCREENSHOT_DIR;
const READ_PATHS = new Set([
  "/api/v1/agents", "/api/v1/approvals", "/api/v1/workflows/runs", "/api/v1/integrations/connections",
  "/api/v1/integrations/catalog", "/api/v1/knowledge/documents", "/api/v1/conversations",
]);
const NAV_LABELS = [["Overview", "Overview"], ["Operations", "Operations"], ["Approvals", "Approvals"],
  ["Conversations", "Conversations"], ["Workflows", "Workflows"], ["Agents", "Agents"], ["Integrations", "Integrations"],
  ["Knowledge", "Knowledge"]];
const PAGES = ["/", "/operations", "/approvals", "/conversations", "/workflows", "/settings/agents",
  "/settings/integrations", "/settings/knowledge"];

function freePort() {
  return new Promise((resolve) => {
    const probe = createServer().listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function startWeb(port, stubPort) {
  const child = spawn(process.execPath, [NEXT_BIN, "start", "-H", "127.0.0.1", "-p", String(port)], {
    cwd: WEB_ROOT,
    env: { ...process.env, PRODUCT_API_ORIGIN: `http://127.0.0.1:${stubPort}`, NEXT_TELEMETRY_DISABLED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let logs = "";
  child.stdout.on("data", (chunk) => { logs += chunk; });
  child.stderr.on("data", (chunk) => { logs += chunk; });
  for (let i = 0; i < 100; i += 1) {
    try {
      if ((await fetch(`http://127.0.0.1:${port}/api/product/health`)).ok) return { child, logs: () => logs };
    } catch { /* not listening yet */ }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  child.kill();
  throw new Error(`web server did not start:\n${logs}`);
}

async function noOverflow(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 0, `${label}: horizontal overflow ${overflow}px`);
}

const results = [];
async function check(name, body) {
  try {
    await body();
    results.push([true, name]);
    console.log(`ok   ${name}`);
  } catch (error) {
    results.push([false, name]);
    console.log(`FAIL ${name}\n     ${String(error?.message ?? error).split("\n").join("\n     ")}`);
  }
}

const stub = await startStub();
const webPort = await freePort();
const web = await startWeb(webPort, stub.port);
const base = `http://127.0.0.1:${webPort}`;
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});

/** Product requests the stub saw since `mark`, excluding health. */
const since = (mark) => stub.requests.slice(mark).filter((r) => r.path !== "/health");

async function newPage(viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const dialogs = [];
  page.on("dialog", async (dialog) => { dialogs.push(dialog.message()); await dialog.dismiss(); });
  // The browser may talk to the same-origin Web app only.
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return url.origin === base ? route.continue() : route.abort();
  });
  return { context, page, dialogs };
}

async function connect(page, key = GOOD) {
  const input = page.locator("#shell-product-api-key");
  await input.fill(key);
  await page.locator(".session-control").getByRole("button", { name: "Connect" }).click();
  assert.equal(await input.count() === 0 ? "" : await input.inputValue(), "");
}

async function noKeyAnywhere(page, key = GOOD) {
  const storage = await page.evaluate(() => ({
    local: localStorage.length, session: sessionStorage.length, cookie: document.cookie,
    url: location.href, html: document.documentElement.outerHTML,
  }));
  assert.equal(storage.local, 0, "localStorage is empty");
  assert.equal(storage.session, 0, "sessionStorage is empty");
  assert.equal(storage.cookie, "", "no cookie");
  assert.ok(!storage.url.includes(key), "key not in the URL");
  assert.ok(!storage.html.includes(key), "key not in the DOM or attributes");
  const databases = await page.evaluate(async () => (indexedDB.databases ? (await indexedDB.databases()).length : 0));
  assert.equal(databases, 0, "no IndexedDB database");
  const cookies = await page.context().cookies();
  assert.equal(cookies.length, 0, "no cookie in the browser context");
}

try {
  await check("Overview without a key: title, reachability, connect control, areas; no authenticated call", async () => {
    const { page, context } = await newPage();
    const mark = stub.requests.length;
    await page.goto(`${base}/`);
    await page.getByRole("heading", { level: 1, name: "Overview" }).waitFor();
    assert.equal(await page.locator("h1").count(), 1);
    await page.locator(".overview-welcome").getByText("API online").waitFor();
    await page.getByRole("region", { name: "Connect to Product API" }).waitFor();
    for (const area of ["Operations", "Approvals", "Conversations", "Workflows", "Agents", "Integrations", "Knowledge"]) {
      await page.getByRole("region", { name: "Areas" }).getByRole("link", { name: area, exact: true }).waitFor();
    }
    assert.equal(since(mark).length, 0, "no Product request without a key");
    assert.equal(await page.title(), "Agento");
    await context.close();
  });

  await check("Overview connect: draft cleared, only parallel read calls, no model call, no mutation", async () => {
    const { page, context, dialogs } = await newPage();
    await page.goto(`${base}/`);
    const mark = stub.requests.length;
    const input = page.locator("#overview-product-api-key");
    await input.fill(GOOD);
    await page.getByRole("region", { name: "Connect to Product API" }).getByRole("button", { name: "Connect" }).click();
    await page.getByRole("region", { name: "Operations Agent" }).getByText("Available").waitFor();
    await page.getByRole("region", { name: "Knowledge" }).getByText("Returns policy").waitFor();
    await page.getByRole("region", { name: "Integrations" }).getByText("No integrations are installed in this build.").waitFor();
    await page.getByRole("region", { name: "Conversations" }).getByText("Support inbox · Example Chat (test)").waitFor();
    await page.getByRole("region", { name: "Workflows" }).getByText("Failed").waitFor();
    // Untrusted titles are inert text.
    await page.getByRole("region", { name: "Approvals" }).getByText("<script>alert('x')</script> Refund order 1001").waitFor();
    assert.equal(dialogs.length, 0, "no script ran");
    // Archived documents are not presented as ready knowledge; no document body is shown.
    assert.equal(await page.getByText("Archived SOP").count(), 0);
    // Signals derived from explicit statuses only.
    const signals = page.getByRole("region", { name: "Needs attention" });
    await signals.getByText("1 approval request is awaiting a decision.").waitFor();
    await signals.getByText("1 of the most recent Workflow runs failed or timed out.").waitFor();
    await signals.getByText("1 of the most recent Workflow runs need a person.").waitFor();
    await page.locator(".session-control").getByText("Key accepted").waitFor();
    const calls = since(mark);
    assert.ok(calls.length >= READ_PATHS.size, "every card read");
    for (const call of calls) {
      assert.equal(call.method, "GET", `${call.method} ${call.path}`);
      assert.ok(READ_PATHS.has(call.path), `unexpected Overview call ${call.path}`);
    }
    assert.deepEqual(new Set(calls.map((c) => c.path)), READ_PATHS);
    assert.ok(!calls.some((c) => c.path.startsWith("/api/v1/operations")), "no Operations run, report or ticket");
    const approvals = calls.find((c) => c.path === "/api/v1/approvals");
    assert.equal(approvals.query, "?status=requested");
    await noKeyAnywhere(page);
    // Refresh re-reads (explicitly) and still only reads.
    const refreshMark = stub.requests.length;
    await page.getByRole("button", { name: "Refresh overview" }).click();
    await page.getByRole("region", { name: "Knowledge" }).getByText("Returns policy").waitFor();
    await page.waitForTimeout(300);
    const refreshed = since(refreshMark);
    assert.deepEqual(new Set(refreshed.map((c) => c.path)), READ_PATHS);
    assert.ok(refreshed.every((c) => c.method === "GET"));
    // No polling: nothing more happens on its own.
    const idleMark = stub.requests.length;
    await page.waitForTimeout(1500);
    assert.equal(since(idleMark).length, 0, "no background requests");
    await context.close();
  });

  await check("session persists across client navigation; active route has aria-current; no storage", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/`);
    await connect(page);
    const nav = page.getByRole("navigation", { name: "Product" });
    await nav.getByRole("link", { name: "Approvals" }).click();
    await page.getByRole("heading", { level: 1, name: "Approvals" }).waitFor();
    await page.getByText("<script>alert('x')</script> Refund order 1001").first().waitFor();
    assert.equal(await nav.getByRole("link", { name: "Approvals" }).getAttribute("aria-current"), "page");
    assert.equal(await nav.getByRole("link", { name: "Overview" }).getAttribute("aria-current"), null);
    await nav.getByRole("link", { name: "Conversations" }).click();
    await page.getByRole("heading", { level: 1, name: "Conversations" }).waitFor();
    await page.getByText("Support inbox · Example Chat (test)").waitFor();
    await nav.getByRole("link", { name: "Workflows" }).click();
    await page.getByRole("heading", { level: 1, name: "Workflows" }).waitFor();
    await nav.getByRole("link", { name: "Knowledge" }).click();
    await page.getByRole("heading", { level: 1, name: "Knowledge" }).waitFor();
    await page.getByText("Returns policy").first().waitFor();
    assert.equal(await page.locator('main input[type="password"]').count(), 0, "no per-page key form");
    await noKeyAnywhere(page);
    await context.close();
  });

  await check("a hard reload forgets the key", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/approvals`);
    await page.getByText("Connect to the Product API to view approvals.").waitFor();
    await connect(page);
    await page.getByText("<script>alert('x')</script> Refund order 1001").first().waitFor();
    await page.reload();
    await page.getByText("Connect to the Product API to view approvals.").waitFor();
    await page.locator(".session-control").getByText("Not connected").waitFor();
    assert.equal(await page.getByText("Refund order 1001").count(), 0);
    await context.close();
  });

  await check("disconnect clears the key, store and authenticated data", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/operations`);
    await connect(page);
    await page.locator("#store-id").fill("0a0a0a0a-0000-4000-8000-000000000001");
    await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: "Approvals" }).click();
    await page.getByText("<script>alert('x')</script> Refund order 1001").first().waitFor();
    await page.locator(".session-control").getByRole("button", { name: "Disconnect" }).click();
    await page.getByText("Connect to the Product API to view approvals.").waitFor();
    assert.equal(await page.getByText("Refund order 1001").count(), 0);
    await page.locator(".session-control").getByText("Not connected").waitFor();
    await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: "Operations" }).click();
    assert.equal(await page.locator("#store-id").inputValue(), "", "store cleared");
    await context.close();
  });

  await check("401 marks the key rejected globally", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/`);
    await connect(page, "stub-key-401");
    await page.locator(".session-control").getByText("Key not accepted").waitFor();
    await page.getByRole("region", { name: "Approvals" }).getByText("Product API key not accepted.").waitFor();
    await context.close();
  });

  await check("403 and 503 degrade one card each without rejecting the key", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/`);
    await connect(page, "stub-key-partial");
    await page.getByRole("region", { name: "Approvals" }).getByText("You don't have access to Approvals.").waitFor();
    await page.getByRole("region", { name: "Knowledge" }).getByText("Knowledge is unavailable right now.").waitFor();
    await page.getByRole("region", { name: "Operations Agent" }).getByText("Available").waitFor();
    await page.getByRole("region", { name: "Conversations" }).getByText("Support inbox · Example Chat (test)").waitFor();
    await page.locator(".session-control").getByText("Key accepted").waitFor();
    assert.equal(await page.getByText("Key not accepted").count(), 0);
    await context.close();
  });

  await check("empty states for every Overview card", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/`);
    await connect(page, "stub-key-empty");
    for (const text of ["No approval requests are awaiting a decision.", "No Workflow runs yet.", "No conversations yet.",
      "No integrations are installed in this build.", "No active knowledge documents yet."]) {
      await page.getByText(text).waitFor();
    }
    await context.close();
  });

  await check("quick actions and deep links only open Operations tabs; nothing runs", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/`);
    await connect(page);
    await page.getByRole("region", { name: "Operations Agent" }).getByText("Available").waitFor();
    const mark = stub.requests.length;
    for (const [label, tab] of [["Daily report", "report"], ["Create ticket", "ticket"], ["Analyze operations", "analysis"]]) {
      await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: "Overview" }).click();
      await page.getByRole("region", { name: "Quick actions" }).getByRole("link", { name: label }).click();
      await page.waitForURL(`**/operations?tab=${tab}`);
      await page.locator(`#tab-${tab}[aria-selected="true"]`).waitFor();
    }
    assert.ok(!since(mark).some((c) => c.path.startsWith("/api/v1/operations")), "no Operations request");
    await page.goto(`${base}/operations?tab=command`);
    await page.locator('#tab-command[aria-selected="true"]').waitFor();
    await page.goto(`${base}/operations?tab=unknown`);
    await page.locator('#tab-analysis[aria-selected="true"]').waitFor();
    await context.close();
  });

  await check("Operations regression: explicit analysis and report; store survives navigation; a store change clears results", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/operations`);
    await connect(page);
    const STORE = "0a0a0a0a-0000-4000-8000-000000000001";
    await page.locator("#store-id").fill(STORE);
    await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: "Overview" }).click();
    await page.getByRole("heading", { level: 1, name: "Overview" }).waitFor();
    await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: "Operations" }).click();
    assert.equal(await page.locator("#store-id").inputValue(), STORE, "store survives client navigation");
    const mark = stub.requests.length;
    await page.locator('textarea[name="message"]').fill("Which shipments need attention?");
    await page.getByRole("button", { name: "Run read-only analysis" }).click();
    await page.getByText("Stub analysis: 2 shipments need attention.").waitFor();
    const runs = since(mark).filter((c) => c.path === "/api/v1/operations/runs");
    assert.equal(runs.length, 1);
    assert.equal(runs[0].method, "POST");
    await page.getByRole("tab", { name: "Daily report" }).click();
    await page.getByRole("button", { name: "Load daily report" }).click();
    await page.getByText("Inventory was not part of this report.").waitFor();
    await page.locator("#store-id").fill("0b0b0b0b-0000-4000-8000-000000000002");
    assert.equal(await page.getByText("Stub analysis: 2 shipments need attention.").count(), 0, "result cleared");
    assert.equal(await page.getByText("Inventory was not part of this report.").count(), 0, "report cleared");
    await context.close();
  });

  await check("legacy settings routes redirect to the canonical pages", async () => {
    const { page, context } = await newPage();
    await page.goto(`${base}/settings/approvals`);
    await page.waitForURL(`${base}/approvals`);
    await page.getByRole("heading", { level: 1, name: "Approvals" }).waitFor();
    await page.goto(`${base}/settings/workflows`);
    await page.waitForURL(`${base}/workflows`);
    await page.getByRole("heading", { level: 1, name: "Workflows" }).waitFor();
    await context.close();
  });

  await check("every page: one h1, Agento branding, no internal framework name, keyboard skip link", async () => {
    const { page, context } = await newPage();
    for (const path of PAGES) {
      await page.goto(`${base}${path}`);
      await page.locator("h1").first().waitFor();
      assert.equal(await page.locator("h1").count(), 1, `${path}: one h1`);
      const text = await page.locator("body").innerText();
      assert.ok(text.includes("Agento") && text.includes("AI Operating Layer"), `${path}: branding`);
      assert.ok(!/AgentOS|Agno|FastAPI|Next\.js/.test(text), `${path}: no internal framework name`);
      if (SCREENSHOTS) {
        mkdirSync(SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: join(SCREENSHOTS, `desktop${path.replaceAll("/", "_") || "_"}.png`), fullPage: true });
      }
    }
    await page.goto(`${base}/`);
    await connect(page);
    for (const [label, heading] of NAV_LABELS) {
      await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: label, exact: true }).click();
      await page.getByRole("heading", { level: 1, name: heading }).waitFor();
      assert.equal(await page.locator("h1").count(), 1, `connected ${label}: one h1`);
      await page.waitForTimeout(250);
      await noOverflow(page, `1280px connected ${label}`);
      if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `desktop-connected-${label}.png`), fullPage: true });
    }
    await page.goto(`${base}/`);
    await page.keyboard.press("Tab");
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), "Skip to content");
    await context.close();
  });

  await check("mobile and tablet: no horizontal overflow; the menu is an accessible toggle", async () => {
    for (const viewport of [{ width: 390, height: 844 }, { width: 768, height: 1024 }]) {
      const { page, context } = await newPage(viewport);
      await page.goto(`${base}/`);
      const menu = page.getByRole("button", { name: "Menu" });
      if (viewport.width < 860) {
        await menu.waitFor();
        assert.equal(await menu.getAttribute("aria-expanded"), "false");
        assert.equal(await page.getByRole("navigation", { name: "Product" }).isVisible(), false);
        await menu.click();
        assert.equal(await page.getByRole("button", { name: "Close menu" }).getAttribute("aria-expanded"), "true");
        await connect(page);
        await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: "Approvals" }).click();
        await page.getByRole("heading", { level: 1, name: "Approvals" }).waitFor();
        assert.equal(await page.getByRole("navigation", { name: "Product" }).isVisible(), false, "menu closes on navigation");
      } else {
        await connect(page);
      }
      // Connected: client-side navigation through every area keeps the key.
      for (const [label, heading] of NAV_LABELS) {
        if (viewport.width < 860) await page.getByRole("button", { name: "Menu" }).click();
        await page.getByRole("navigation", { name: "Product" }).getByRole("link", { name: label, exact: true }).click();
        await page.getByRole("heading", { level: 1, name: heading }).waitFor();
        await page.waitForTimeout(250);
        await noOverflow(page, `${viewport.width}px connected ${label}`);
        if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, `w${viewport.width}-connected-${label}.png`), fullPage: true });
      }
      await page.locator(".session-control").getByText("Key accepted").waitFor({ state: "attached" });
      // Not connected (a hard load each time).
      for (const path of PAGES) {
        await page.goto(`${base}${path}`);
        await page.locator("h1").first().waitFor();
        await page.waitForTimeout(150);
        await noOverflow(page, `${viewport.width}px ${path}`);
      }
      await context.close();
    }
  });
} finally {
  await browser.close();
  web.child.kill();
  await new Promise((resolve) => stub.server.close(resolve));
  stub.server.closeAllConnections?.();
}

const logs = web.logs();
assert.ok(!logs.includes(GOOD), "server logs never contain the key");
const failed = results.filter(([ok]) => !ok).length;
console.log(failed ? `\n${failed} browser check(s) failed` : "\nControl Center browser tests passed");
process.exit(failed ? 1 : 0);
