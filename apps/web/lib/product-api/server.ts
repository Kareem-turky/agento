// Server-only Product API proxy (the Operations Console BFF).
//
//   browser --same origin--> /api/product/<fixed route>
//          --> this module --> PRODUCT_API_ORIGIN + <one fixed Product path>
//
// Each BFF route maps to exactly ONE existing Product API route through a constant in
// UPSTREAM below: there is no catch-all, no client-supplied path or origin, and no way
// to reach AgentOS routes. The upstream origin comes only from the server environment
// (never NEXT_PUBLIC_*, never the request). Only Authorization (and, for ticket
// creation, Idempotency-Key) are forwarded; bodies and responses are size-capped,
// redirects are never followed, nothing is cached, stored or logged, and every
// transport problem becomes one fixed 502 answer.
import "server-only";

export const MAX_REQUEST_BYTES = 16 * 1024;
export const MAX_RESPONSE_BYTES = 1024 * 1024;
export const UPSTREAM_TIMEOUT_MS = 120_000;

/** The five exact Product API routes this BFF can reach. Nothing else. */
export const UPSTREAM = {
  health: { method: "GET", path: "/health" },
  operationsRuns: { method: "POST", path: "/api/v1/operations/runs" },
  dailyReport: { method: "GET", path: "/api/v1/operations/reports/daily" },
  tickets: { method: "POST", path: "/api/v1/operations/tickets" },
  ticketCommands: { method: "GET", path: "/api/v1/operations/tickets/commands" },
} as const;

export type UpstreamRoute = keyof typeof UPSTREAM;

type ProxyOptions = {
  /** Forward the caller's Product API key (every route except health). */
  authorization: boolean;
  /** Forward exactly one Idempotency-Key (ticket creation only). */
  idempotencyKey?: boolean;
  /** Query parameters allowed through, each at most once (GET routes). */
  query?: readonly string[];
  /** Forward the JSON request body (POST routes). */
  body?: boolean;
};

const NO_STORE = "no-store";
const BEARER = /^Bearer [\x21-\x7e]{1,1024}$/;
const IDEMPOTENCY_KEY = /^[\x21-\x2b\x2d-\x7e]{1,255}$/; // printable ASCII, no comma
const REQUEST_ID = /^[A-Za-z0-9-]{1,64}$/;

function json(status: number, body: unknown, requestId?: string): Response {
  const headers = new Headers({
    "Content-Type": "application/json",
    "Cache-Control": NO_STORE,
  });
  if (requestId) headers.set("X-Request-ID", requestId);
  return new Response(JSON.stringify(body), { status, headers });
}

/** The single fixed transport failure: no origin, address, error text or stack. */
export function unavailable(): Response {
  return json(502, { detail: "Product API unavailable" });
}

function tooLarge(): Response {
  return json(413, { detail: "Request too large" });
}

function unsupportedQuery(): Response {
  return json(422, { detail: "Unsupported query parameters" });
}

/**
 * The configured Product API origin, or null when it is missing or unsafe.
 * Only scheme, host and port are allowed: no credentials, path, query or fragment.
 */
export function productApiOrigin(raw: string | undefined = process.env.PRODUCT_API_ORIGIN): string | null {
  if (!raw || raw !== raw.trim() || raw.includes("?") || raw.includes("#")) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (!url.hostname || url.username || url.password) return null;
  if (url.search || url.hash || url.pathname !== "/") return null;
  return url.origin;
}

async function readBounded(stream: ReadableStream<Uint8Array> | null, limit: number): Promise<Uint8Array | null> {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

function forwardedQuery(request: Request, allowed: readonly string[]): URLSearchParams | null {
  const incoming = new URL(request.url).searchParams;
  const outgoing = new URLSearchParams();
  const seen = new Set<string>();
  for (const [key, value] of incoming) {
    if (!allowed.includes(key) || seen.has(key)) return null;
    seen.add(key);
    outgoing.append(key, value);
  }
  return outgoing;
}

/** Proxy one browser request to its one fixed Product API route. */
export async function proxyToProduct(request: Request, route: UpstreamRoute, options: ProxyOptions): Promise<Response> {
  const target = UPSTREAM[route];
  const origin = productApiOrigin();
  if (origin === null) return unavailable();

  let search = "";
  const query = forwardedQuery(request, options.query ?? []);
  if (query === null) return unsupportedQuery();
  if (query.size > 0) search = `?${query.toString()}`;

  // A fresh, BFF-owned header set: browser headers are never copied wholesale.
  const headers = new Headers({ Accept: "application/json" });
  if (options.authorization) {
    const authorization = request.headers.get("authorization");
    if (authorization && BEARER.test(authorization)) headers.set("Authorization", authorization);
  }
  if (options.idempotencyKey) {
    const key = request.headers.get("idempotency-key");
    if (key && IDEMPOTENCY_KEY.test(key)) headers.set("Idempotency-Key", key);
  }

  let body: Uint8Array | undefined;
  if (options.body) {
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (!Number.isFinite(declared) || declared > MAX_REQUEST_BYTES) return tooLarge();
    const read = await readBounded(request.body, MAX_REQUEST_BYTES).catch(() => null);
    if (read === null) return tooLarge();
    body = read;
    headers.set("Content-Type", "application/json");
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${origin}${target.path}${search}`, {
      method: target.method,
      headers,
      body: body as BodyInit | undefined,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return unavailable();
  }

  // Redirects are never followed or passed on; anything but a JSON answer is refused.
  if (upstream.type === "opaqueredirect" || (upstream.status >= 300 && upstream.status < 400)) {
    await upstream.body?.cancel().catch(() => undefined);
    return unavailable();
  }
  const contentType = upstream.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    await upstream.body?.cancel().catch(() => undefined);
    return unavailable();
  }
  const declaredLength = Number(upstream.headers.get("content-length") ?? "0");
  if (!Number.isFinite(declaredLength) || declaredLength > MAX_RESPONSE_BYTES) {
    await upstream.body?.cancel().catch(() => undefined);
    return unavailable();
  }
  const raw = await readBounded(upstream.body, MAX_RESPONSE_BYTES).catch(() => null);
  if (raw === null) return unavailable();
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch {
    return unavailable();
  }

  const requestId = upstream.headers.get("x-request-id") ?? undefined;
  return json(upstream.status, parsed, requestId && REQUEST_ID.test(requestId) ? requestId : undefined);
}
