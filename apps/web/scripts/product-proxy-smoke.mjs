// Offline smoke test of the Product BFF (Node standard library only).
//
//   npm run build && npm run smoke:proxy
//
// Starts a local stub Product API and the built Next app on 127.0.0.1 only, calls the
// BFF routes, and checks what the stub actually received. No real Product backend,
// model, provider or external network is involved.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const NEXT_BIN = join(WEB_ROOT, "node_modules", "next", "dist", "bin", "next");
const MARK = randomBytes(6).toString("hex");
const API_KEY = `smoke-product-key-marker-${MARK}`; // obviously test-only
const COOKIE_MARK = `smoke-cookie-marker-${MARK}`;
const BROWSER_REQUEST_ID = `smoke-browser-request-id-${MARK}`;
const STORE = "0b0b0b0b-0000-4000-8000-000000000001";
const COMMAND = "0c0c0c0c-0000-4000-8000-000000000001";
const UPSTREAM_REQUEST_ID = "5f5f5f5f-0000-4000-8000-000000000001";

let failures = 0;
function check(condition, name) {
  if (condition) {
    console.log(`ok   ${name}`);
  } else {
    failures += 1;
    console.log(`FAIL ${name}`);
  }
}

// ----- stub Product API ------------------------------------------------------------------

const received = [];
let mode = "normal";

const REPORT = {
  request_id: UPSTREAM_REQUEST_ID,
  report: {
    store_id: STORE, business_date: "2026-01-15", timezone: "Europe/Berlin",
    window_start: "2026-01-14T23:00:00Z", window_end: "2026-01-15T23:00:00Z",
    generated_at: "2026-01-16T08:00:00Z",
    metrics: {
      orders_created: 0, order_status_counts: [{ status: "pending", count: 0 }],
      shipments_shipped: 0, shipment_status_counts: [{ status: "shipped", count: 0 }],
      affected_orders: 0,
    },
    findings: [], findings_total: 0, findings_truncated: false,
    coverage: { orders: "created_in_business_day", shipments: "shipped_in_business_day",
                inventory: "not_included", inventory_reason: "store_scoped_inventory_query_unavailable" },
  },
};

function reply(res, status, body, extra = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "X-Request-ID": UPSTREAM_REQUEST_ID,
    "Set-Cookie": `upstream=${MARK}; Path=/`,
    "WWW-Authenticate": "Bearer",
    Server: "stub-product-api",
    ...extra,
  });
  res.end(text);
}

const stub = createServer((req, res) => {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    received.push({ method: req.method, url: req.url, headers: req.headers,
                    body: Buffer.concat(chunks).toString("utf8") });
    const path = new URL(req.url, "http://stub").pathname;
    if (mode === "redirect") return reply(res, 302, {}, { Location: "http://198.51.100.1/elsewhere" });
    if (mode === "html") {
      res.writeHead(200, { "Content-Type": "text/html" });
      return res.end("<html>not json</html>");
    }
    if (mode === "huge") return reply(res, 200, { padding: "x".repeat(2 * 1024 * 1024) });
    if (path === "/health") return reply(res, 200, { status: "ok", agent_runtime: { status: "ready" } });
    if (path === "/api/v1/operations/runs") return reply(res, 200, { request_id: UPSTREAM_REQUEST_ID, message: "stub analysis" });
    if (path === "/api/v1/operations/reports/daily") return reply(res, 200, REPORT);
    if (path === "/api/v1/operations/tickets") {
      return reply(res, 201, { request_id: UPSTREAM_REQUEST_ID, command_id: COMMAND, status: "verified",
                               reason: "verified", ticket_id: STORE, replayed: false, persistence_complete: true });
    }
    if (path === "/api/v1/operations/tickets/commands") {
      return reply(res, 200, { request_id: UPSTREAM_REQUEST_ID, command_id: COMMAND, status: "verified",
                               reason: "verified", ticket_id: STORE, created_at: "2026-01-16T08:00:00Z",
                               updated_at: "2026-01-16T08:00:01Z" });
    }
    return reply(res, 404, { detail: "Not Found" });
  });
});

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server.address().port)));
}

async function freePort() {
  const probe = createServer();
  const port = await listen(probe);
  await new Promise((resolve) => probe.close(resolve));
  return port;
}

// ----- the built Next app ----------------------------------------------------------------

function startNext(port, origin) {
  const env = { ...process.env, PRODUCT_API_ORIGIN: origin, NEXT_TELEMETRY_DISABLED: "1",
                HTTP_PROXY: "", HTTPS_PROXY: "", http_proxy: "", https_proxy: "", NODE_ENV: "production" };
  const child = spawn(process.execPath, [NEXT_BIN, "start", "-H", "127.0.0.1", "-p", String(port)],
                      { cwd: WEB_ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
  child.logs = "";
  child.stdout.on("data", (chunk) => { child.logs += chunk; });
  child.stderr.on("data", (chunk) => { child.logs += chunk; });
  return child;
}

async function waitReady(base) {
  for (let i = 0; i < 100; i += 1) {
    try {
      const response = await fetch(`${base}/`, { redirect: "manual" });
      if (response.status === 200) return;
    } catch { /* not yet */ }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Next did not start");
}

function stop(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    child.once("exit", resolve);
    child.kill("SIGTERM");
  });
}

function onlyAllowedHeaders(headers, allowed) {
  const extra = Object.keys(headers).filter((name) => !allowed.includes(name));
  return extra.length === 0 ? true : extra;
}

// Node's fetch adds these transport headers itself (cache-control/pragma: no-cache come
// from the fetch standard's "no-store" cache mode); nothing else may reach the Product.
const TRANSPORT = ["host", "connection", "content-length", "accept-encoding", "accept-language",
                   "sec-fetch-mode", "user-agent", "accept", "cache-control", "pragma"];

async function main() {
  const stubPort = await listen(stub);
  const origin = `http://127.0.0.1:${stubPort}`;
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const next = startNext(port, origin);
  const hostile = {
    Authorization: `Bearer ${API_KEY}`,
    Cookie: `session=${COOKIE_MARK}`,
    "X-Request-ID": BROWSER_REQUEST_ID,
    "X-Forwarded-For": "203.0.113.9",
    Forwarded: "for=203.0.113.9",
    "Proxy-Authorization": "Basic Zm9vOmJhcg==",
    Referer: "http://203.0.113.9/",
    Origin: "http://203.0.113.9",
    "X-Custom-Header": "custom",
  };
  let secondNext = null;
  try {
    await waitReady(base);

    // 1. health reaches only /health, with no credential.
    received.length = 0;
    let response = await fetch(`${base}/api/product/health`, { headers: hostile });
    let body = await response.json();
    check(response.status === 200 && body.status === "ok", "health: 200 from the stub");
    check(received.length === 1 && received[0].method === "GET" && received[0].url === "/health", "health: only GET /health upstream");
    check(!("authorization" in received[0].headers), "health: no Authorization forwarded");
    check(response.headers.get("cache-control") === "no-store", "health: Cache-Control no-store");

    // 2. operations run: exact path, Authorization and JSON body only.
    received.length = 0;
    const runBody = JSON.stringify({ message: "hello", store_id: STORE });
    response = await fetch(`${base}/api/product/operations/runs`, {
      method: "POST", headers: { ...hostile, "Content-Type": "application/json", Host: "evil.example" }, body: runBody,
    });
    body = await response.json();
    check(response.status === 200 && body.message === "stub analysis", "runs: 200 from the stub");
    const run = received[0];
    check(received.length === 1 && run.method === "POST" && run.url === "/api/v1/operations/runs", "runs: exact POST path");
    check(run.headers.authorization === `Bearer ${API_KEY}`, "runs: Authorization forwarded");
    check(run.body === runBody && run.headers["content-type"] === "application/json", "runs: JSON body forwarded");
    check(!("cookie" in run.headers), "runs: browser Cookie not forwarded");
    check(!("x-request-id" in run.headers), "runs: browser X-Request-ID not forwarded");
    check(run.headers.host === `127.0.0.1:${stubPort}`, "runs: browser Host not forwarded");
    const runExtra = onlyAllowedHeaders(run.headers, [...TRANSPORT, "authorization", "content-type"]);
    check(runExtra === true, `runs: no other browser header forwarded ${runExtra === true ? "" : runExtra}`);
    check(!("idempotency-key" in run.headers), "runs: no Idempotency-Key forwarded");
    check(response.headers.get("set-cookie") === null, "runs: upstream Set-Cookie not forwarded");
    check(response.headers.get("www-authenticate") === null && response.headers.get("server") === null,
          "runs: upstream WWW-Authenticate/Server not forwarded");
    check(response.headers.get("x-request-id") === UPSTREAM_REQUEST_ID, "runs: Product X-Request-ID preserved");
    check(response.headers.get("cache-control") === "no-store", "runs: Cache-Control no-store");

    // 3. ticket: Authorization + exactly one Idempotency-Key, exact path.
    received.length = 0;
    const key = "8a8a8a8a-0000-4000-8000-000000000001";
    response = await fetch(`${base}/api/product/operations/tickets`, {
      method: "POST",
      headers: { ...hostile, "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify({ store_id: STORE, title: "t", description: "d" }),
    });
    body = await response.json();
    check(response.status === 201 && body.status === "verified", "tickets: 201 from the stub");
    const ticket = received[0];
    check(received.length === 1 && ticket.method === "POST" && ticket.url === "/api/v1/operations/tickets", "tickets: exact POST path");
    check(ticket.headers.authorization === `Bearer ${API_KEY}` && ticket.headers["idempotency-key"] === key,
          "tickets: Authorization and one Idempotency-Key forwarded");
    const ticketExtra = onlyAllowedHeaders(ticket.headers, [...TRANSPORT, "authorization", "content-type", "idempotency-key"]);
    check(ticketExtra === true, `tickets: no other browser header forwarded ${ticketExtra === true ? "" : ticketExtra}`);
    check(run.headers["user-agent"] !== undefined && !String(run.headers["user-agent"]).includes("203.0.113"),
          "runs: browser Referer/Origin/User-Agent values not forwarded");

    // 4. daily report: only store_id and business_date.
    received.length = 0;
    response = await fetch(`${base}/api/product/operations/reports/daily?store_id=${STORE}&business_date=2026-01-15`,
                           { headers: hostile });
    check(response.status === 200, "report: 200 from the stub");
    check(received.length === 1 && received[0].url === `/api/v1/operations/reports/daily?store_id=${STORE}&business_date=2026-01-15`,
          "report: exact path with only the allowed query");
    received.length = 0;
    response = await fetch(`${base}/api/product/operations/reports/daily?store_id=${STORE}`, { headers: hostile });
    check(response.status === 200 && received[0].url === `/api/v1/operations/reports/daily?store_id=${STORE}`,
          "report: blank date omitted");

    // 5. command status: only command_id.
    received.length = 0;
    response = await fetch(`${base}/api/product/operations/tickets/commands?command_id=${COMMAND}`, { headers: hostile });
    check(response.status === 200 && received[0].url === `/api/v1/operations/tickets/commands?command_id=${COMMAND}`,
          "commands: exact path with only command_id");
    check(!("idempotency-key" in received[0].headers), "commands: no Idempotency-Key forwarded");

    // 9-10. extra and duplicate query parameters are rejected before the upstream call.
    received.length = 0;
    const rejected = [
      `/api/product/operations/reports/daily?store_id=${STORE}&timezone=UTC`,
      `/api/product/operations/reports/daily?store_id=${STORE}&store_id=${STORE}`,
      `/api/product/operations/tickets/commands?command_id=${COMMAND}&command_id=${COMMAND}`,
      `/api/product/operations/tickets/commands?command_id=${COMMAND}&path=/agents`,
      `/api/product/health?x=1`,
    ];
    for (const path of rejected) {
      response = await fetch(`${base}${path}`, { headers: hostile });
      check(response.status === 422, `query rejected: ${path.split("?")[0]} (${response.status})`);
    }
    check(received.length === 0, "rejected queries never reached the upstream");

    // 11-12. unknown routes and AgentOS-like paths cannot be proxied.
    received.length = 0;
    for (const path of ["/api/product/agents", "/api/product/info", "/api/product/config", "/api/product/sessions",
                        "/api/product/teams", "/api/product/runs", "/api/product/docs", "/api/product/openapi.json",
                        "/api/product/metrics", "/api/product/operations/runs/agents",
                        "/api/product/operations/agents", "/api/product/..%2F..%2Fagents", "/api/product/unknown"]) {
      response = await fetch(`${base}${path}`, { headers: hostile });
      check(response.status === 404, `not proxied: ${path} (${response.status})`);
    }
    response = await fetch(`${base}/api/product/operations/runs`, { headers: hostile });
    check(response.status === 405, `GET on the POST-only runs route is refused (${response.status})`);
    check(received.length === 0, "no unknown or AgentOS-like path reached the upstream");

    // 19. oversized request bodies are refused before the upstream call.
    received.length = 0;
    const big = JSON.stringify({ message: "x".repeat(20 * 1024), store_id: STORE });
    response = await fetch(`${base}/api/product/operations/runs`, {
      method: "POST", headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json" }, body: big,
    });
    check(response.status === 413, `oversized request: 413 (${response.status})`);
    check(received.length === 0, "oversized request never reached the upstream");

    // 13, 17. upstream redirects are neither followed nor passed on.
    received.length = 0;
    mode = "redirect";
    response = await fetch(`${base}/api/product/operations/tickets/commands?command_id=${COMMAND}`,
                           { headers: hostile, redirect: "manual" });
    body = await response.json();
    check(response.status === 502 && body.detail === "Product API unavailable", "redirect: fixed 502");
    check(response.headers.get("location") === null, "redirect: Location not forwarded");
    check(received.length === 1, "redirect: not followed");

    // Non-JSON and oversized upstream responses are refused.
    mode = "html";
    response = await fetch(`${base}/api/product/health`);
    check(response.status === 502, "non-JSON upstream: fixed 502");
    mode = "huge";
    response = await fetch(`${base}/api/product/health`);
    check(response.status === 502, "oversized upstream response: fixed 502");
    mode = "normal";

    // 20 / UI secret guard: rendered HTML and client bundles carry no key, origin or upstream path.
    response = await fetch(`${base}/`);
    const html = await response.text();
    check(response.status === 200 && html.includes("Operations Console"), "console page renders");
    check(!html.includes(API_KEY) && !html.includes(origin) && !html.includes(String(stubPort)),
          "rendered HTML has no key, origin or upstream port");
    const staticDir = join(WEB_ROOT, ".next", "static");
    const bundles = [];
    (function walk(dir) {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (name.endsWith(".js")) bundles.push(readFileSync(path, "utf8"));
      }
    })(staticDir);
    const clientCode = bundles.join("\n");
    check(bundles.length > 0 && !clientCode.includes("PRODUCT_API_ORIGIN") && !clientCode.includes("/api/v1/operations"),
          "client bundles contain no server origin variable or upstream Product path");

    // 14. upstream unavailable (stub stopped).
    await new Promise((resolve) => stub.close(resolve));
    stub.closeAllConnections?.();
    response = await fetch(`${base}/api/product/operations/runs`, {
      method: "POST", headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json" }, body: runBody,
    });
    const unavailableText = await response.text();
    check(response.status === 502 && JSON.parse(unavailableText).detail === "Product API unavailable",
          "unavailable upstream: fixed 502");
    check(!unavailableText.includes(origin) && !unavailableText.includes(API_KEY) && !/ECONN|fetch failed|Error/.test(unavailableText),
          "unavailable upstream: no origin, key or error detail in the response");
    check(response.headers.get("cache-control") === "no-store", "unavailable upstream: no-store");

    // Invalid origin configuration fails closed (credentials in the origin).
    const badPort = await freePort();
    secondNext = startNext(badPort, `http://user:pass@127.0.0.1:${stubPort}`);
    await waitReady(`http://127.0.0.1:${badPort}`);
    response = await fetch(`http://127.0.0.1:${badPort}/api/product/health`);
    body = await response.json();
    check(response.status === 502 && body.detail === "Product API unavailable", "invalid PRODUCT_API_ORIGIN: fixed 502");
  } finally {
    await stop(next);
    if (secondNext) await stop(secondNext);
    stub.close();
  }

  // 20. no credential or marker in the Next server logs.
  const logs = next.logs + (secondNext ? secondNext.logs : "");
  check(!logs.includes(API_KEY) && !logs.includes(MARK), "server logs contain no key or marker");

  if (failures > 0) {
    console.log(`\n${failures} check(s) failed`);
    process.exit(1);
  }
  console.log("\nProduct proxy smoke test passed");
}

main().catch(() => {
  console.log("smoke test crashed");
  process.exit(1);
});
