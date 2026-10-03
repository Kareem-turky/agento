// GET /api/product/conversations?limit=&connection_id= -> Product API GET
// /api/v1/conversations (read-only; there is no ingest, webhook or send route).
import { proxyToProduct } from "../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "conversations", { authorization: true, query: ["limit", "connection_id"] });
}
