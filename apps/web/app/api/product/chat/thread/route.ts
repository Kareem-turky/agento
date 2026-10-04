// GET /api/product/chat/thread?thread_id= -> Product API GET /api/v1/chat/thread
// (Task 042: one thread with its turns and ticket proposals).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "chatThread", { authorization: true, query: ["thread_id"] });
}
