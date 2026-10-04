// GET  /api/product/chat/threads?store_id= -> Product API GET  /api/v1/chat/threads
// POST /api/product/chat/threads           -> Product API POST /api/v1/chat/threads
// (Task 042: the employee's own chat threads in one store; creating one calls no model).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "chatThreads", { authorization: true, query: ["store_id"] });
}

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "chatThreadCreate", { authorization: true, body: true });
}
