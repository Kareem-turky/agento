// GET /api/product/knowledge/documents -> Product API GET /api/v1/knowledge/documents
// (document metadata; no bodies).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeDocuments", { authorization: true });
}
