// POST /api/product/knowledge/query -> Product API POST /api/v1/knowledge/query
// (bounded retrieval preview; returns untrusted references, never an answer).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeQuery", { authorization: true, body: true });
}
