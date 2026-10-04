// GET  /api/product/chat/turns?thread_id= -> Product API GET  /api/v1/chat/turns
// POST /api/product/chat/turns            -> Product API POST /api/v1/chat/turns
// (Task 042: one idempotent turn {thread_id, turn_id, message}; no Idempotency-Key).
import { MAX_CHAT_REQUEST_BYTES, proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "chatTurns", { authorization: true, query: ["thread_id"] });
}

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "chatTurnSubmit", {
    authorization: true,
    body: true,
    maxRequestBytes: MAX_CHAT_REQUEST_BYTES,
  });
}
